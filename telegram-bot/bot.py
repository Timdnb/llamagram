#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()


@dataclass
class BotConfig:
    telegram_bot_token: str
    allowed_user_ids: set[int]
    allowed_chat_ids: set[int]
    image_support_enabled: bool
    llama_server_url: str
    mcp_proxy_url: str
    llm_model_alias: str
    agent_name: str
    enabled_mcp_servers: list[str]
    max_history_messages: int
    min_seconds_between_messages: float
    max_tool_steps: int
    max_image_bytes: int
    max_images_per_message: int


def _parse_int_set(value: str) -> set[int]:
    out: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if token:
            out.add(int(token))
    return out


def _read_active_agent_name(base_dir: Path) -> str:
    active_agent_file = base_dir / ".state" / "active-agent"
    if not active_agent_file.exists():
        raise RuntimeError("Missing .state/active-agent. Start the stack with ./llm.sh start <agent> first.")

    agent = active_agent_file.read_text(encoding="utf-8").strip()
    if not agent:
        raise RuntimeError("Empty .state/active-agent. Restart with ./llm.sh start <agent>.")

    return agent


def _read_enabled_mcp_servers(base_dir: Path) -> list[str]:
    active_mcp_file = base_dir / ".state" / "active-mcp-config"
    if not active_mcp_file.exists():
        raise RuntimeError("Missing .state/active-mcp-config. Start the stack with ./llm.sh start <agent> first.")

    rendered_path_text = active_mcp_file.read_text(encoding="utf-8").strip()
    if not rendered_path_text:
        raise RuntimeError("Empty .state/active-mcp-config. Restart with ./llm.sh start <agent>.")

    rendered_path = Path(rendered_path_text)
    if not rendered_path.is_absolute():
        rendered_path = (base_dir / rendered_path).resolve()

    if not rendered_path.exists():
        raise RuntimeError(f"Active MCP config not found: {rendered_path}")

    payload = json.loads(rendered_path.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise RuntimeError(f"Invalid mcpServers in active MCP config: {rendered_path}")

    return [name for name in servers.keys() if isinstance(name, str) and name]


def _read_stack_port_from_defaults(base_dir: Path, key: str) -> int:
    defaults_path = base_dir / "infra" / "llama" / "defaults.env"
    if not defaults_path.exists():
        raise RuntimeError(f"Missing defaults file: {defaults_path}")

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.+)\s*$")
    value_expr: str | None = None
    for line in defaults_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            value_expr = match.group(1).strip()
            break

    if value_expr is None:
        raise RuntimeError(f"{key} is missing in {defaults_path}")

    # Supports entries like ${LLAMA_PORT:-8002} or plain numeric values.
    fallback_match = re.match(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]+)\}$", value_expr)
    if fallback_match:
        value_expr = fallback_match.group(1).strip()

    value_expr = value_expr.strip('"\'')
    if not value_expr:
        raise RuntimeError(f"Empty {key} value in {defaults_path}")

    try:
        return int(value_expr)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {key} value in {defaults_path}: {value_expr}") from exc


def _discover_model_alias(llama_server_url: str) -> str:
    try:
        response = requests.get(f"{llama_server_url}/v1/models", timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not reach llama-server at {llama_server_url}. Start stack first with ./llm.sh start <agent>."
        ) from exc

    payload = response.json()
    models = payload.get("data")
    if not isinstance(models, list) or not models:
        raise RuntimeError(f"No models returned by llama-server at {llama_server_url}/v1/models")

    model_id = models[0].get("id") if isinstance(models[0], dict) else None
    if not isinstance(model_id, str) or not model_id.strip():
        raise RuntimeError(f"Invalid /v1/models response from llama-server: {payload}")

    return model_id.strip()


def _load_config() -> BotConfig:
    base_dir = Path(__file__).resolve().parents[1]
    active_agent_name = _read_active_agent_name(base_dir)
    enabled_mcp_servers = _read_enabled_mcp_servers(base_dir)
    llama_port = _read_stack_port_from_defaults(base_dir, "LLAMA_PORT")
    mcp_proxy_port = _read_stack_port_from_defaults(base_dir, "MCP_PROXY_PORT")
    llama_server_url = f"http://127.0.0.1:{llama_port}"
    mcp_proxy_url = f"http://127.0.0.1:{mcp_proxy_port}"
    llm_model_alias = _discover_model_alias(llama_server_url)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    allowed_users = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not allowed_users:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is required")

    return BotConfig(
        telegram_bot_token=token,
        allowed_user_ids=_parse_int_set(allowed_users),
        allowed_chat_ids=_parse_int_set(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")),
        image_support_enabled=os.getenv("IMAGE_SUPPORT_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        llama_server_url=llama_server_url,
        mcp_proxy_url=mcp_proxy_url,
        llm_model_alias=llm_model_alias,
        agent_name=active_agent_name,
        enabled_mcp_servers=enabled_mcp_servers,
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
        min_seconds_between_messages=float(os.getenv("MIN_SECONDS_BETWEEN_MESSAGES", "0.8")),
        max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "8")),
        max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024))),
        max_images_per_message=int(os.getenv("MAX_IMAGES_PER_MESSAGE", "4")),
    )


CONFIG = _load_config()

BASE_DIR = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = BASE_DIR / "agents" / CONFIG.agent_name / "system.md"
if not SYSTEM_PROMPT_PATH.exists():
    raise RuntimeError(f"Agent system prompt not found: {SYSTEM_PROMPT_PATH}")
SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()

LOG_DIR = Path(os.getenv("LOG_DIR", "./logs")).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("llamagram-telegram")


def _redact_for_log(text: str) -> str:
    redactions = [
        CONFIG.telegram_bot_token,
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
    ]

    output = text
    for secret in redactions:
        value = (secret or "").strip()
        if not value:
            continue
        output = output.replace(value, "[REDACTED]")
        output = output.replace(quote(value, safe=""), "[REDACTED]")
        output = output.replace(f"bot{value}", "bot[REDACTED]")
    return output


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _redact_for_log(super().format(record))


def _install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        existing = handler.formatter
        if existing:
            handler.setFormatter(RedactingFormatter(existing._style._fmt, existing.datefmt))
        else:
            handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))


def _audit(event: str, payload: dict[str, Any]) -> None:
    safe_payload = _redact_for_log(json.dumps(payload, ensure_ascii=True))
    logger.info("%s %s", event, safe_payload)


_install_log_redaction()

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

chat_histories: dict[str, deque[dict[str, Any]]] = {}
last_message_at: dict[int, float] = {}
mcp_sessions: dict[str, str] = {}
chat_tools_cache: list[dict[str, Any]] = []
chat_tool_server_map: dict[str, str] = {}
chat_tools_loaded_at = 0.0
TOOLS_TTL_SECONDS = 30


@dataclass
class PendingMediaGroup:
    chat_id: int
    media_group_id: str
    messages: list[Any]
    added_at: float


pending_media_groups: dict[str, PendingMediaGroup] = {}
MEDIA_GROUP_TIMEOUT_SECONDS = 2


def _message_has_image(message: Any) -> bool:
    if getattr(message, "photo", None):
        return True

    document = getattr(message, "document", None)
    mime_type = getattr(document, "mime_type", None) if document else None
    if mime_type and mime_type.startswith("image/"):
        return True

    file_name = getattr(document, "file_name", "") if document else ""
    guessed_type, _ = mimetypes.guess_type(file_name or "")
    return bool(guessed_type and guessed_type.startswith("image/"))


def _image_message_filter_reason(message: Any) -> str:
    if getattr(message, "photo", None):
        return ""

    document = getattr(message, "document", None)
    if document is None:
        return "No image attached"

    mime_type = getattr(document, "mime_type", None)
    if mime_type and mime_type.startswith("image/"):
        return ""

    guessed_type, _ = mimetypes.guess_type(getattr(document, "file_name", "") or "")
    if guessed_type and guessed_type.startswith("image/"):
        return ""

    return "Unsupported attachment type"


def _build_user_content(text: str, images: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if not images:
        return text

    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})

    for image in images:
        parts.append({"type": "image_url", "image_url": {"url": image["data_url"]}})

    return parts


async def _download_image_attachment(message: Any) -> tuple[list[dict[str, Any]], str]:
    images: list[dict[str, Any]] = []

    if getattr(message, "photo", None):
        image_size = message.photo[-1]
        image_file = await image_size.get_file()
        mime_type = "image/jpeg"
        file_size = getattr(image_size, "file_size", None)
        file_name = "telegram-photo.jpg"
    elif getattr(message, "document", None):
        document = message.document
        mime_type = getattr(document, "mime_type", None) or mimetypes.guess_type(getattr(document, "file_name", "") or "")[0]
        image_file = await document.get_file()
        file_size = getattr(document, "file_size", None)
        file_name = getattr(document, "file_name", None) or "telegram-image"
    else:
        return images, "No image attached"

    if not mime_type or not mime_type.startswith("image/"):
        return images, _image_message_filter_reason(message)

    if isinstance(file_size, int) and file_size > CONFIG.max_image_bytes:
        return images, f"Image is too large. Max allowed is {CONFIG.max_image_bytes} bytes."

    data = await image_file.download_as_bytearray()
    if len(data) > CONFIG.max_image_bytes:
        return images, f"Image is too large. Max allowed is {CONFIG.max_image_bytes} bytes."

    encoded = base64.b64encode(bytes(data)).decode("ascii")
    images.append(
        {
            "data_url": f"data:{mime_type};base64,{encoded}",
            "mime_type": mime_type,
            "file_name": file_name,
        }
    )
    return images, ""


async def _download_images_from_messages(messages: list[Any]) -> tuple[list[dict[str, Any]], str]:
    all_images: list[dict[str, Any]] = []

    for message in messages:
        images, error = await _download_image_attachment(message)
        if error:
            return [], error
        all_images.extend(images)

    if len(all_images) > CONFIG.max_images_per_message:
        return [], f"Too many images. Maximum allowed is {CONFIG.max_images_per_message} images per message."

    return all_images, ""


def _is_authorized(update: Update) -> tuple[bool, str]:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False, "Cannot identify user/chat"

    if user.id not in CONFIG.allowed_user_ids:
        return False, "This bot is allowlisted. Access denied."

    if CONFIG.allowed_chat_ids and chat.id not in CONFIG.allowed_chat_ids:
        return False, "This chat is not allowlisted for the bot."

    return True, ""


def _history_key(chat_id: int) -> str:
    # Keep memory isolated per Telegram chat id.
    return f"chat:{chat_id}"


def _normalize_text_for_telegram(text: str) -> str:
    normalized = text.replace("\r\n", "\n").strip()
    # Some local model responses contain escaped newlines as literal characters.
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")
    if "\\t" in normalized and "\t" not in normalized:
        normalized = normalized.replace("\\t", "\t")
    return normalized


def _rate_limited(chat_id: int) -> bool:
    now = time.monotonic()
    prev = last_message_at.get(chat_id, 0.0)
    if now - prev < CONFIG.min_seconds_between_messages:
        return True
    last_message_at[chat_id] = now
    return False


def _history_for_chat(chat_id: int) -> deque[dict[str, Any]]:
    key = _history_key(chat_id)
    history = chat_histories.get(key)
    if history is None:
        history = deque(maxlen=CONFIG.max_history_messages)
        chat_histories[key] = history
    return history


async def _process_user_message(update: Update, text: str, images: list[dict[str, Any]]) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    if _rate_limited(chat_id):
        await update.message.reply_text("You are sending messages too quickly. Please wait a moment.")
        return

    if images and not CONFIG.image_support_enabled:
        await update.message.reply_text(
            "Image support is currently disabled. Set IMAGE_SUPPORT_ENABLED=true and restart the bot."
        )
        return

    if not text and not images:
        return

    message_type = "image" if images else "text"
    _audit(
        "user_message",
        {
            "type": message_type,
            "chars": len(text),
            "images": len(images),
            "chat_id": chat_id,
            "user_id": update.effective_user.id,
        },
    )

    history_deque = _history_for_chat(chat_id)
    history = list(history_deque)
    history.append({"role": "user", "content": _build_user_content(text, images)})

    try:
        ok, reply, updated_history = _execute_chat_loop(
            chat_id=chat_id,
            user_id=update.effective_user.id,
            message_history=history,
        )
    except Exception as exc:
        logger.exception("chat loop failed")
        await update.message.reply_text(f"Error: {exc}")
        return

    history_deque.clear()
    for item in updated_history[-CONFIG.max_history_messages :]:
        history_deque.append(item)

    response_text = _normalize_text_for_telegram(reply)
    if not ok:
        response_text = _normalize_text_for_telegram(f"Failed: {reply}")

    _audit(
        "assistant_reply",
        {
            "ok": ok,
            "chars": len(response_text),
            "chat_id": chat_id,
            "user_id": update.effective_user.id,
        },
    )

    await update.message.reply_text(response_text or "(No content returned)")


async def _process_pending_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    await asyncio.sleep(MEDIA_GROUP_TIMEOUT_SECONDS)
    if key not in pending_media_groups:
        return

    group = pending_media_groups.pop(key)
    caption = ""
    for message in group.messages:
        caption = (getattr(message, "caption", None) or "").strip()
        if caption:
            break

    images, error = await _download_images_from_messages(group.messages)
    if error:
        if update.message:
            await update.message.reply_text(error)
        return

    await _process_user_message(update, text=caption, images=images)


def _with_system_prompt(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Inject system prompt as first message on every request.
    return [{"role": "system", "content": SYSTEM_PROMPT}, *messages]


def _mcp_rpc(server_name: str, method: str, params: dict[str, Any], request_id: str, timeout: int = 30) -> dict[str, Any]:
    session_id = mcp_sessions.get(server_name, f"mcp-{uuid.uuid4().hex[:8]}")
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    response = requests.post(
        f"{CONFIG.mcp_proxy_url}/servers/{server_name}/mcp",
        headers={**MCP_HEADERS, "mcp-session-id": session_id},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    if server_name not in mcp_sessions and method == "initialize":
        mcp_sessions[server_name] = session_id

    data = response.json()
    return data


def _ensure_mcp_session(server_name: str) -> str:
    if server_name in mcp_sessions:
        return mcp_sessions[server_name]

    sid = f"mcp-{uuid.uuid4().hex[:8]}"
    init_payload = {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "llamagram-telegram", "version": "0.1.0"},
        "capabilities": {},
    }

    response = requests.post(
        f"{CONFIG.mcp_proxy_url}/servers/{server_name}/mcp",
        headers={**MCP_HEADERS, "mcp-session-id": sid},
        json={
            "jsonrpc": "2.0",
            "id": f"{sid}-init",
            "method": "initialize",
            "params": init_payload,
        },
        timeout=30,
    )
    response.raise_for_status()

    requests.post(
        f"{CONFIG.mcp_proxy_url}/servers/{server_name}/mcp",
        headers={**MCP_HEADERS, "mcp-session-id": sid},
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        timeout=20,
    ).raise_for_status()

    mcp_sessions[server_name] = sid
    return sid


def _mcp_list_tools(server_name: str) -> list[dict[str, Any]]:
    sid = _ensure_mcp_session(server_name)
    response = requests.post(
        f"{CONFIG.mcp_proxy_url}/servers/{server_name}/mcp",
        headers={**MCP_HEADERS, "mcp-session-id": sid},
        json={
            "jsonrpc": "2.0",
            "id": f"{sid}-tools-{uuid.uuid4().hex[:6]}",
            "method": "tools/list",
            "params": {},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "tools/list failed"))
    tools = payload.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("invalid tools/list response")
    return tools


def _refresh_tools(force: bool = False) -> list[dict[str, Any]]:
    global chat_tools_loaded_at, chat_tools_cache, chat_tool_server_map

    now = time.time()
    if not force and chat_tools_cache and (now - chat_tools_loaded_at) < TOOLS_TTL_SECONDS:
        return chat_tools_cache

    loaded: list[dict[str, Any]] = []
    server_map: dict[str, str] = {}

    for server in CONFIG.enabled_mcp_servers:
        for tool in _mcp_list_tools(server):
            name = tool.get("name")
            if not name:
                continue
            loaded.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                }
            )
            server_map[name] = server

    if not loaded:
        raise RuntimeError("No tools loaded from enabled MCP servers")

    chat_tool_server_map = server_map
    chat_tools_cache = loaded
    chat_tools_loaded_at = now
    return loaded


def _mcp_call_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    sid = _ensure_mcp_session(server_name)
    response = requests.post(
        f"{CONFIG.mcp_proxy_url}/servers/{server_name}/mcp",
        headers={**MCP_HEADERS, "mcp-session-id": sid},
        json={
            "jsonrpc": "2.0",
            "id": f"{sid}-call-{uuid.uuid4().hex[:6]}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        return {"ok": False, "error": payload["error"].get("message", "MCP error")}

    result = payload.get("result", {})
    is_error = bool(result.get("isError"))

    text_parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(item.get("text", ""))

    raw = "\n".join(text_parts).strip()
    if is_error:
        return {"ok": False, "error": raw or "MCP tool returned error"}

    if not raw:
        return {"ok": True, "data": result}

    try:
        return {"ok": True, "data": json.loads(raw)}
    except Exception:
        return {"ok": True, "data": raw}


def _call_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "model": CONFIG.llm_model_alias,
        "messages": _with_system_prompt(messages),
        "tools": tools,
        "tool_choice": "auto",
    }
    response = requests.post(
        f"{CONFIG.llama_server_url}/v1/chat/completions",
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    return response.json()


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _execute_chat_loop(chat_id: int, user_id: int, message_history: list[dict[str, Any]]) -> tuple[bool, str, list[dict[str, Any]]]:
    tools = _refresh_tools()

    for _ in range(CONFIG.max_tool_steps):
        result = _call_llm(message_history, tools)
        choice = (result.get("choices") or [{}])[0]
        message = choice.get("message", {})
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            final_text = _normalize_text_for_telegram(message.get("content") or "") or "(No content returned)"
            message_history.append({"role": "assistant", "content": final_text})
            return True, final_text, message_history

        message_history.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            tool_name = fn.get("name", "")
            tool_args = _parse_tool_args(fn.get("arguments"))
            tool_call_id = tool_call.get("id", f"local_{uuid.uuid4().hex[:8]}")

            server = chat_tool_server_map.get(tool_name)
            if not server:
                tool_result = {"ok": False, "error": f"unknown tool: {tool_name}"}
            else:
                tool_result = _mcp_call_tool(server, tool_name, tool_args)

            _audit(
                "tool_call",
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "server": server or "unknown",
                    "tool": tool_name,
                    "args": tool_args,
                    "ok": tool_result.get("ok", False),
                    "error": tool_result.get("error", ""),
                },
            )

            message_history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": json.dumps(tool_result, ensure_ascii=True),
                }
            )

    return False, "Stopped after too many tool steps.", message_history


async def _reject_if_unauthorized(update: Update) -> bool:
    ok, reason = _is_authorized(update)
    if ok:
        return False
    if update.message:
        await update.message.reply_text(reason)
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text(
        f"llamagram bot online (agent={CONFIG.agent_name}).\n"
        "Commands:\n"
        "/help - show help\n"
        "/images - image support status\n"
        "/whoami - show Telegram IDs\n"
        "/reset - clear this chat history"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    await update.message.reply_text(
        "Commands:\n"
        "/start - status\n"
        "/help - help\n"
        "/images - image support status\n"
        "/whoami - show IDs\n"
        "/reset - clear chat history"
    )


async def images_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if update.message:
        status = "enabled" if CONFIG.image_support_enabled else "disabled"
        await update.message.reply_text(f"Image support: {status}")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if update.message:
        await update.message.reply_text(
            f"user_id={user.id if user else 'unknown'}\nchat_id={chat.id if chat else 'unknown'}"
        )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_unauthorized(update):
        return
    if not update.effective_chat:
        return
    chat_histories.pop(_history_key(update.effective_chat.id), None)
    await update.message.reply_text("History cleared for this chat.")


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = (update.message.text or "").strip()
    await _process_user_message(update, text=user_text, images=[])


async def image_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not _message_has_image(message):
        return

    if not CONFIG.image_support_enabled:
        await message.reply_text(
            "Image support is currently disabled. Set IMAGE_SUPPORT_ENABLED=true and restart the bot."
        )
        return

    media_group_id = getattr(message, "media_group_id", None)
    if media_group_id:
        key = f"{message.chat_id}_{media_group_id}"
        if key not in pending_media_groups:
            pending_media_groups[key] = PendingMediaGroup(
                chat_id=message.chat_id,
                media_group_id=media_group_id,
                messages=[],
                added_at=time.time(),
            )

        pending_media_groups[key].messages.append(message)
        asyncio.create_task(_process_pending_media_group(update, context, key))
        return

    text = (message.caption or "").strip()
    images, error = await _download_image_attachment(message)
    if error:
        await message.reply_text(error)
        return

    await _process_user_message(update, text=text, images=images)


def main() -> None:
    logger.info("Starting llamagram Telegram bot")
    logger.info("Agent=%s system_prompt=%s", CONFIG.agent_name, SYSTEM_PROMPT_PATH)
    logger.info("Enabled MCP servers=%s", CONFIG.enabled_mcp_servers)
    logger.info("Image support=%s", CONFIG.image_support_enabled)

    app = Application.builder().token(CONFIG.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("images", images_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, image_message))
    app.run_polling()


if __name__ == "__main__":
    main()
