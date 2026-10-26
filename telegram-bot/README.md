# Telegram bot

Connects Telegram to the active llama-server model and MCP tools.

## Setup

From `telegram-bot/`:

```bash
cp .env.example .env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`. Optionally restrict
`TELEGRAM_ALLOWED_CHAT_IDS`. Set `IMAGE_SUPPORT_ENABLED=true` to allow images;
the bot also checks that the running server reports vision support.

Start the stack from the repository root:

```bash
./llm.sh start assistant
```

Wait for the model to load (`./llm.sh logs llama`), then:

```bash
cd telegram-bot
./start.sh
```

Stop the bot with Ctrl+C before changing agents, models, or MCP configuration,
then start it again afterward. Edits to the active `system.md` apply immediately.
The system prompt is injected by this bot; it does not configure the server's web UI.

## Behavior

- `/start`, `/help`, `/system`, `/images`, `/whoami`, `/reset`.
- `/images` checks the running model. Unsupported images are rejected before download.
- History is per chat, held in memory, and cleared on restart or `/reset`.
- History trimming preserves complete tool turns; the latest turn is always kept.
- Different chats can run concurrently; each chat keeps its messages in order.
- Tools load at startup and include their server name, e.g. `time__get_current_time`.
- Replies support basic Markdown; long replies are split into plain text.
- Logs redact the Telegram token and record metadata without message contents.

## Code and checks

`bot.py` handles Telegram and chat state; `config.py` loads settings; `chat.py`
handles model/MCP requests; `formatting.py` formats replies.

```bash
.venv/bin/python -m unittest discover -s tests -v
```
