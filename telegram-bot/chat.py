"""Async clients for llama-server and the active agent's MCP tools."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx

from config import BotConfig
from formatting import strip_reasoning

logger = logging.getLogger(__name__)
Message = dict[str, Any]


def trim_history(messages: list[Message], limit: int) -> list[Message]:
    """Drop whole turns, retaining the latest even when it exceeds the budget."""
    starts = [index for index, message in enumerate(messages) if message["role"] == "user"]
    start = next((index for index in starts if len(messages) - index <= limit), starts[-1])
    return messages[start:]


class MCPClient:
    def __init__(self, http: httpx.AsyncClient, url: str):
        self.http = http
        self.url = url
        self.sessions: dict[str, str] = {}
        self.tools: list[dict[str, Any]] = []
        self.routes: dict[str, tuple[str, str]] = {}

    async def rpc(self, server: str, method: str, params: dict, *, notify: bool = False) -> dict:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            payload["id"] = uuid.uuid4().hex
        headers = {"Accept": "application/json, text/event-stream"}
        if server in self.sessions:
            headers["mcp-session-id"] = self.sessions[server]
        response = await self.http.post(
            f"{self.url}/servers/{server}/mcp",
            headers=headers,
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        if method == "initialize" and "mcp-session-id" in response.headers:
            self.sessions[server] = response.headers["mcp-session-id"]
        if notify:
            return {}
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"MCP {server}/{method}: {data['error']['message']}")
        return data["result"]

    async def load_tools(self, servers: tuple[str, ...]) -> None:
        self.tools.clear()
        self.routes.clear()
        for server in servers:
            await self.rpc(
                server,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "llamagram", "version": "0.1.0"},
                    "capabilities": {},
                },
            )
            await self.rpc(server, "notifications/initialized", {}, notify=True)
            result = await self.rpc(server, "tools/list", {})
            for tool in result["tools"]:
                # Different servers can expose tools with the same name (e.g. health).
                name = f"{server}__{tool['name']}"
                self.routes[name] = (server, tool["name"])
                self.tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": tool["inputSchema"],
                    },
                })

    async def call(self, name: str, arguments: str) -> dict:
        server, tool = self.routes[name]
        params = json.loads(arguments)
        if not isinstance(params, dict):
            raise ValueError("Tool arguments must be a JSON object")
        result = await self.rpc(server, "tools/call", {"name": tool, "arguments": params})
        logger.info(
            "tool_call server=%s tool=%s ok=%s",
            server, tool, not result.get("isError", False),
        )
        return result


class ChatClient:
    def __init__(self, config: BotConfig, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.mcp = MCPClient(http, config.mcp_url)
        self.model = ""

    async def initialize(self) -> None:
        response = await self.http.get(f"{self.config.llama_url}/v1/models", timeout=20)
        response.raise_for_status()
        self.model = response.json()["data"][0]["id"]
        await self.mcp.load_tools(self.config.mcp_servers)

    async def supports_images(self) -> bool:
        response = await self.http.get(f"{self.config.llama_url}/props", timeout=10)
        response.raise_for_status()
        return response.json().get("modalities", {}).get("vision") is True

    async def complete(self, history: list[Message]) -> str:
        for _ in range(self.config.max_tool_steps):
            # Reading on each call keeps prompt edits live without a cache or globals.
            prompt = self.config.system_prompt_path.read_text(encoding="utf-8").strip()
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": prompt}, *history],
            }
            if self.mcp.tools:
                payload.update(tools=self.mcp.tools, tool_choice="auto")
            response = await self.http.post(
                f"{self.config.llama_url}/v1/chat/completions",
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            content = strip_reasoning(message.get("content") or "")
            calls = message.get("tool_calls") or []
            assistant = {"role": "assistant", "content": content}
            if calls:
                assistant["tool_calls"] = calls
            history.append(assistant)
            if not calls:
                return content
            for call in calls:
                function = call["function"]
                result = await self.mcp.call(function["name"], function["arguments"])
                history.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
        raise RuntimeError("Stopped after too many tool steps")
