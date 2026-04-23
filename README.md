# llamagram 🦙

**llamagram** is a starter repo for running a local LLM with MCP tools, chatting with it over Telegram. It is local-first by default: local model, local stack, and local/custom MCP servers in this repo. You can still plug in third-party MCP servers when needed.

## How it works

1. Define an **agent** (model path, system prompt, MCP tools)
2. Run a local **llama-server** with your GGUF model
3. Expose tools via **mcp-proxy** (local/custom MCP servers by default)
4. Chat through a **Telegram bot** that bridges it all together

---

## Getting Started

### Included example agents

- `assistant`: general starter profile
- `beepboop`: playful assistant, no MCP tools
- `timekeeper`: always responds with current time, uses the local `time` MCP server

You can use local/custom MCP servers from this repo by default, and you can also wire in third-party MCP servers by editing each agent's `mcp.config.json`.

### Prerequisites

- Linux or macOS
- `llama-server` binary ([llama.cpp](https://github.com/ggml-org/llama.cpp))
- `uv` and `uvx`
- Python 3.10+

### 1. Point to your llama-server binary

Edit `infra/llama/defaults.env`:

```env
LLAMA_SERVER_BIN=/path/to/llama.cpp/build/bin/llama-server
```

### 2. Configure your agent

Edit `agents/assistant/agent.env`:

```env
AGENT_LLAMA_MODEL=/path/to/your/model.gguf
AGENT_LLAMA_ALIAS=your/model-name
```

### 3. Add llama-server flags (optional)

Any `AGENT_LLAMA_FLAG_*` var gets passed as a flag to `llama-server`:

```env
AGENT_LLAMA_FLAG_CTX_SIZE=32768   # → --ctx-size 32768
AGENT_LLAMA_FLAG_JINJA=true       # → --jinja
```

> `--model`, `--alias`, and `--port` are managed by the stack — don't set these.

### 4. Start the stack 🚀

```bash
./llm.sh start assistant
```

This validates your config, starts `llama-server`, and launches `mcp-proxy`.

### 5. Set up the Telegram bot

```bash
cd telegram-bot
cp .env.example .env
```

Fill in:
- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/botfather)
- `TELEGRAM_ALLOWED_USER_IDS` — your Telegram user ID(s), comma-separated
- `IMAGE_SUPPORT_ENABLED` — set `true` if your model supports vision input

### 6. Start the bot

```bash
cd telegram-bot
./start.sh
```

The bot auto-discovers the running model and active agent, and starts relaying your Telegram messages.

---

## Controller commands

```bash
./llm.sh list-agents
./llm.sh start <agent>
./llm.sh stop
./llm.sh restart <agent>
./llm.sh status
./llm.sh logs llama
./llm.sh logs mcp
```

---

## Reference

### Agent contract

Each agent lives in `agents/<name>/` and requires three files:

| File | Purpose |
|---|---|
| `agent.env` | Model path, alias, llama flags |
| `mcp.config.json` | MCP tool configuration |
| `system.md` | System prompt |

Required vars in `agent.env`: `AGENT_LLAMA_MODEL`, `AGENT_LLAMA_ALIAS`, `AGENT_MCP_LOCAL_TIMEZONE`

### One active agent at a time

llamagram runs a single agent stack at a time. Starting a second agent while one is running is blocked — use `restart` to switch. The active agent is tracked in `.state/active-agent`.

### Repo layout

```
agents/          one folder per agent
mcps/            custom MCP servers (includes a `time` example)
telegram-bot/    Telegram bridge
bin/             shared shell helpers
infra/llama/     stack defaults
.state/          runtime files, logs, PIDs (generated at runtime)
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).