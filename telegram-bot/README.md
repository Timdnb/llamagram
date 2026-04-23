# Telegram Bot

This process connects Telegram to your local OpenAI-compatible `llama-server` and MCP tool stack.

Important behavior:

- The bot uses the currently active agent from `../.state/active-agent` (written by `./llm.sh start <agent>`).
- On every request to `/v1/chat/completions`, the bot prepends `agents/<active-agent>/system.md` as the first `system` message.
- MCP tools are loaded from the active rendered MCP config path in `../.state/active-mcp-config`.
- Only one active agent is supported at a time.
- llama-server and mcp-proxy ports are read from `../infra/llama/defaults.env`.
- Model alias is discovered from the running llama-server via `/v1/models`.
- Photo messages and image documents can be forwarded to a vision-capable model when `IMAGE_SUPPORT_ENABLED=true`.
- Conversation history is isolated per Telegram `chat_id`.
- Logs include redacted audit metadata (message type, char count, image count, chat_id, user_id).

## Setup

1. Copy env file:

```bash
cp .env.example .env
```

2. Edit `.env`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`
- `IMAGE_SUPPORT_ENABLED=true` if the selected model can read images
- `MAX_IMAGE_BYTES` and `MAX_IMAGES_PER_MESSAGE` to tune attachment limits

3. Start local stack from repo root:

```bash
./llm.sh start assistant
```

The bot expects this step to be done first so `../.state/active-agent` and `../.state/active-mcp-config` exist.

To switch to another agent, run:

```bash
./llm.sh restart <agent>
```

4. Run the bot:

```bash
./start.sh
```

## Commands

- `/start`
- `/help`
- `/system`
- `/images`
- `/whoami`
- `/reset`
