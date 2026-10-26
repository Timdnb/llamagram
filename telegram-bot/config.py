"""Load the bot's environment and the stack's active agent configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent


def _ids(value: str) -> frozenset[int]:
    return frozenset(int(item) for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class BotConfig:
    token: str
    allowed_user_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]
    agent_name: str
    system_prompt_path: Path
    llama_url: str
    mcp_url: str
    mcp_servers: tuple[str, ...]
    image_support_enabled: bool = False
    max_history_messages: int = 20
    min_seconds_between_messages: float = 0.8
    max_tool_steps: int = 8
    max_image_bytes: int = 10 * 1024 * 1024
    max_images_per_message: int = 4

    @classmethod
    def load(cls, root: Path = ROOT_DIR) -> BotConfig:
        load_dotenv(root / ".env")
        load_dotenv(root / "telegram-bot" / ".env")
        defaults = dotenv_values(root / "infra" / "llama" / "defaults.env")

        token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
        allowed_users = _ids(os.environ["TELEGRAM_ALLOWED_USER_IDS"])
        if not token or not allowed_users:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS are required")

        state = root / ".state"
        agent = (state / "active-agent").read_text().strip()
        if not agent:
            raise ValueError("No active agent. Run ./llm.sh start <agent> first.")
        config_path = root / (state / "active-mcp-config").read_text().strip()
        servers = json.loads(config_path.read_text())["mcpServers"]
        prompt_path = root / "agents" / agent / "system.md"
        if not prompt_path.is_file():
            raise FileNotFoundError(prompt_path)

        def port(key: str) -> int:
            return int(os.environ.get(key) or defaults[key])

        config = cls(
            token=token,
            allowed_user_ids=allowed_users,
            allowed_chat_ids=_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")),
            agent_name=agent,
            system_prompt_path=prompt_path,
            llama_url=f"http://127.0.0.1:{port('LLAMA_PORT')}",
            mcp_url=f"http://127.0.0.1:{port('MCP_PROXY_PORT')}",
            mcp_servers=tuple(servers),
            image_support_enabled=os.getenv("IMAGE_SUPPORT_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
            min_seconds_between_messages=float(os.getenv("MIN_SECONDS_BETWEEN_MESSAGES", "0.8")),
            max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "8")),
            max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024))),
            max_images_per_message=int(os.getenv("MAX_IMAGES_PER_MESSAGE", "4")),
        )
        limits = (
            config.max_history_messages,
            config.max_tool_steps,
            config.max_image_bytes,
            config.max_images_per_message,
        )
        if any(limit < 1 for limit in limits) or config.min_seconds_between_messages < 0:
            raise ValueError("Limits must be positive and the message interval must be nonnegative")
        return config
