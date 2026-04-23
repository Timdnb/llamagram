# llamagram

llamagram is a generic local-LLM + MCP + Telegram starter repo.

It is designed for one clear workflow:

1. Create an agent profile (`agents/<agent>`)
2. Run a local OpenAI-compatible `llama-server` with a GGUF model
3. Expose MCP tools through `mcp-proxy`
4. Chat with that setup from Telegram

## Repo Layout

- `agents/` one folder per agent (`agent.env`, `mcp.config.json`, `system.md`)
- `mcps/` custom MCP servers (example: `time`)
- `telegram-bot/` Telegram bridge process for text and image chat
- `bin/` shared shell helpers used by `llm.sh`
- `infra/llama/defaults.env` stack defaults (ports, llama binary path)
- `.state/` runtime-generated files, logs, and PID files (created at runtime)

## Prerequisites

- Linux/macOS shell
- `llama-server` binary available locally
- `uv` and `uvx`
- Python 3.10+

## Quick Start

### 1. Set your llama-server binary path

Edit `infra/llama/defaults.env`:

```bash
LLAMA_SERVER_BIN=/path/to/llama.cpp/build/bin/llama-server
```

Replace `/path/to/llama.cpp/build/bin/llama-server` with the actual path to your llama-server binary.

### 2. Configure your agent model

Edit `agents/assistant/agent.env` and set your model path:

```env
AGENT_LLAMA_MODEL=/path/to/your/model.gguf
AGENT_LLAMA_ALIAS=your/model-name
```

### 3. (Optional) Add llama-server flags

In `agents/assistant/agent.env`, add any additional flags using `AGENT_LLAMA_FLAG_*`:

```env
# becomes: --ctx-size 32768
AGENT_LLAMA_FLAG_CTX_SIZE=32768

# becomes: --jinja
AGENT_LLAMA_FLAG_JINJA=true
```

Mapping rule: `AGENT_LLAMA_FLAG_<UPPER_SNAKE_CASE>` → `--lower-kebab-case`.

**Note:** `--model`, `--alias`, and `--port` are managed by the stack; do not override them via flags.

### 4. Start the LLM + MCP stack

```bash
./llm.sh start assistant
```

This will:
- Validate agent configuration
- Start `llama-server` with your model
- Start `mcp-proxy` with any enabled MCP tools
- Create `.state/active-agent` (read by Telegram bot)

### 5. Configure Telegram bot

```bash
cd telegram-bot
cp .env.example .env
```

Edit `.env` to set:

- `TELEGRAM_BOT_TOKEN` - Get from [@BotFather](https://t.me/botfather)
- `TELEGRAM_ALLOWED_USER_IDS` - Your Telegram user ID(s) (comma-separated)
- `IMAGE_SUPPORT_ENABLED` - `true` if your model supports image input (use mmproj with llama-server)

### 6. Start the Telegram bot

```bash
cd telegram-bot
./start.sh
```

The bot will:
- Auto-discover the running llama-server model
- Read the active agent from `.state/active-agent`
- Listen for Telegram messages and bridge them to your local stack

## Controller Commands

```bash
./llm.sh list-agents
./llm.sh start <agent>
./llm.sh stop
./llm.sh restart <agent>
./llm.sh status
./llm.sh logs llama
./llm.sh logs mcp
```

## Single Active Agent Model

llamagram currently runs one active agent stack at a time.

- `./llm.sh start <agent>` makes that agent active and starts `llama-server` + `mcp-proxy`.
- The Telegram bot always serves the active agent from `.state/active-agent`.
- Starting a different agent while services are already running is blocked by a guardrail.
- To switch agents, use `./llm.sh restart <agent>` (or `stop` then `start`).

## Agent Contract

Each agent directory must contain:

- `agent.env`
- `mcp.config.json`
- `system.md`

Required env vars in `agent.env`:

- `AGENT_LLAMA_MODEL`
- `AGENT_LLAMA_ALIAS`
- `AGENT_MCP_LOCAL_TIMEZONE`

Optional llama flags in `agent.env`:

- `AGENT_LLAMA_FLAG_*` (dynamic mapping to `llama-server` flags)

Notes:

- `--model`, `--alias`, and `--port` are managed by the stack and should not be set via `AGENT_LLAMA_FLAG_*`.

## MCP Example

The included `time` MCP server is a placeholder example and a smoke test target for new setups.
