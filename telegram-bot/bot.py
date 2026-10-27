#!/usr/bin/env python3
"""Telegram commands, image uploads, and per-chat conversation state."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx
from telegram import Message, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from chat import ChatClient, trim_history
from chat import Message as ChatMessage
from config import BASE_DIR, BotConfig
from formatting import reply

logger = logging.getLogger(__name__)
HELP = (
    "/start - status\n"
    "/help - show help\n"
    "/system - show active system prompt\n"
    "/images - image support status\n"
    "/whoami - show Telegram IDs\n"
    "/reset - clear this chat history"
)
ALBUM_WAIT_SECONDS = 2


@dataclass
class ChatState:
    history: list[ChatMessage] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_message_at: float = 0


def setup_logging(token: str) -> None:
    class RedactingFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            text = super().format(record)
            for secret in (token, quote(token, safe="")):
                text = text.replace(secret, "[REDACTED]")
            return text

    log_dir = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_dir / "bot.log", encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(), handlers=handlers, force=True
    )


class TelegramBot:
    def __init__(self, config: BotConfig, chat: ChatClient):
        self.config = config
        self.chat = chat
        self.chats: dict[int, ChatState] = defaultdict(ChatState)
        self.albums: dict[tuple[int, str], list[Message]] = {}

    async def startup(self, application: Application) -> None:
        try:
            await self.chat.initialize()
        except Exception:
            await self.chat.http.aclose()
            raise
        logger.info(
            "Bot ready: agent=%s model=%s tools=%d",
            self.config.agent_name,
            self.chat.model,
            len(self.chat.mcp.tools),
        )

    async def shutdown(self, application: Application) -> None:
        await self.chat.http.aclose()

    async def authorized(self, update: Update) -> bool:
        user, chat, message = (
            update.effective_user,
            update.effective_chat,
            update.message,
        )
        if user is None or chat is None or message is None:
            return False
        allowed = user.id in self.config.allowed_user_ids and (
            not self.config.allowed_chat_ids or chat.id in self.config.allowed_chat_ids
        )
        if not allowed:
            await reply(message, "This bot is allowlisted. Access denied.")
        return allowed

    async def image_support_status(self) -> tuple[bool, str]:
        if not self.config.image_support_enabled:
            return (
                False,
                "Image support: disabled in bot settings (IMAGE_SUPPORT_ENABLED=false).",
            )
        try:
            supported = await self.chat.supports_images()
        except httpx.HTTPError as exc:
            logger.warning("Could not check model vision support: %s", exc)
            return (
                False,
                "Image support: unavailable (could not check the running model).",
            )
        if not supported:
            return (
                False,
                "Image support: disabled (the running model has no vision support).",
            )
        return True, "Image support: enabled."

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if message is None:
            return
        command = message.text.split()[0].split("@")[0]
        # This command lets a user find the IDs needed to configure the allowlist.
        if command == "/whoami":
            await reply(
                message,
                f"user_id={update.effective_user.id}\nchat_id={message.chat_id}",
            )
            return
        if not await self.authorized(update):
            return
        if command == "/reset":
            state = self.chats[message.chat_id]
            async with state.lock:
                state.history.clear()
            text = "History cleared for this chat."
        elif command == "/system":
            text = self.config.system_prompt_path.read_text(encoding="utf-8")
        elif command == "/images":
            _, text = await self.image_support_status()
        elif command == "/start":
            text = f"llamagram bot online (agent={self.config.agent_name}).\n{HELP}"
        else:
            text = HELP
        await reply(message, text)

    async def text_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if await self.authorized(update):
            await self.respond(update.message, (update.message.text or "").strip(), [])

    async def download_image(self, message: Message) -> str:
        if message.photo:
            attachment = message.photo[-1]
            mime_type = "image/jpeg"
        else:
            attachment = message.document
            mime_type = (
                attachment.mime_type
                or mimetypes.guess_type(attachment.file_name or "")[0]
            )
        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError("Unsupported image type")
        if (attachment.file_size or 0) > self.config.max_image_bytes:
            raise ValueError(f"Image exceeds {self.config.max_image_bytes} bytes")
        remote = await attachment.get_file()
        data = await remote.download_as_bytearray()
        if len(data) > self.config.max_image_bytes:
            raise ValueError(f"Image exceeds {self.config.max_image_bytes} bytes")
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"

    async def image_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self.authorized(update):
            return
        message = update.message
        if message.media_group_id:
            key = (message.chat_id, message.media_group_id)
            if key not in self.albums:
                self.albums[key] = []
                # One managed task per album; errors go through the app's error handler.
                context.application.create_task(self.process_album(key), update=update)
            self.albums[key].append(message)
        else:
            await self.process_images([message])

    async def process_album(self, key: tuple[int, str]) -> None:
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        await self.process_images(self.albums.pop(key))

    async def process_images(self, messages: list[Message]) -> None:
        enabled, status = await self.image_support_status()
        if not enabled:
            await reply(messages[0], status)
            return
        if len(messages) > self.config.max_images_per_message:
            raise ValueError(
                f"Maximum {self.config.max_images_per_message} images per message"
            )
        images = [await self.download_image(message) for message in messages]
        caption = next(
            (message.caption.strip() for message in messages if message.caption), ""
        )
        await self.respond(messages[0], caption, images)

    async def respond(self, message: Message, text: str, images: list[str]) -> None:
        if not text and not images:
            return
        state = self.chats[message.chat_id]
        # Different chats can run concurrently; each chat keeps ordered turns.
        async with state.lock:
            now = time.monotonic()
            if now - state.last_message_at < self.config.min_seconds_between_messages:
                await reply(
                    message,
                    "You are sending messages too quickly. Please wait a moment.",
                )
                return
            state.last_message_at = now
            content: str | list[dict] = text
            if images:
                content = ([{"type": "text", "text": text}] if text else []) + [
                    {"type": "image_url", "image_url": {"url": image}}
                    for image in images
                ]
            history = [*state.history, {"role": "user", "content": content}]
            logger.info(
                "user_message chat_id=%s chars=%d images=%d",
                message.chat_id,
                len(text),
                len(images),
            )
            answer = await self.chat.complete(history)
            state.history = trim_history(history, self.config.max_history_messages)
            await reply(message, answer, markdown=True)
            logger.info(
                "assistant_reply chat_id=%s chars=%d", message.chat_id, len(answer)
            )

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Request failed", exc_info=context.error)
        if isinstance(update, Update) and update.message:
            await reply(update.message, "Request failed. See the bot log for details.")

    def application(self) -> Application:
        app = (
            Application.builder()
            .token(self.config.token)
            .concurrent_updates(16)
            .post_init(self.startup)
            .post_shutdown(self.shutdown)
            .build()
        )
        commands = ["start", "help", "system", "images", "whoami", "reset"]
        app.add_handler(CommandHandler(commands, self.command))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_message)
        )
        app.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.image_message)
        )
        app.add_error_handler(self.error)
        return app


def main() -> None:
    config = BotConfig.load()
    setup_logging(config.token)
    chat = ChatClient(config, httpx.AsyncClient())
    TelegramBot(config, chat).application().run_polling()


if __name__ == "__main__":
    main()
