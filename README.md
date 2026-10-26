# llamagram 🦙

Run a local LLM with MCP tools and chat with it through Telegram.
One agent stack runs at a time.

## Setup

Requires Linux, Bash 4.3+, Python 3.11+, `uv`/`uvx`, and a built
[llama-server](https://github.com/ggml-org/llama.cpp).
Qwen3.8 needs an MTP-aware runtime; these profiles are verified with build b11669.

```bash
cp .env.example .env
```

Set `LLAMA_SERVER_BIN` and `LLAMA_MODELS_DIR` in `.env`. Download your GGUF weights
under that model directory using the relative paths in `models/*.env`.
`LOCAL_TIMEZONE` defaults to UTC.

```bash
./llm.sh start assistant
./llm.sh logs llama
```

Once the model is ready, open `http://127.0.0.1:8002` or start the
[Telegram bot](telegram-bot/README.md).

## Agents and models

| File | Owns |
|---|---|
| `models/<model>.env` | GGUF path and llama-server flags |
| `agents/<agent>/agent.env` | Default model name and MCP settings |
| `agents/<agent>/mcp.config.json` | Enabled MCP servers |
| `agents/<agent>/system.md` | Telegram system prompt |

Starter agents: **assistant** (general help with time tools), **beepboop**
(playful, no tools), and **timekeeper** (current time).

Choose an agent's default in its `agent.env`:

```env
AGENT_MODEL=qwen36-iq4
AGENT_MCP_LOCAL_TIMEZONE=${LOCAL_TIMEZONE:-UTC}
```

Override that model for a run, keeping the same prompt and tools:

```bash
./llm.sh list-models
./llm.sh restart assistant qwen38-iq3
./llm.sh restart assistant              # restore the agent's default
```

To add a model, create `models/<name>.env`:

```env
LLAMA_MODEL="${LLAMA_MODELS_DIR}/your-model.gguf"
LLAMA_FLAG_CTX_SIZE=32768
LLAMA_FLAG_N_GPU_LAYERS=auto
LLAMA_FLAG_JINJA=true
```

`LLAMA_FLAG_*` maps to CLI flags: `CTX_SIZE` becomes `--ctx-size`. `true` adds a
switch, `false` omits it, and other values are passed literally. Alias and port
are managed by the controller. Model files can source another model file to
share settings; the included vision variant adds its own projector this way.

## MCP tools

Keep tool selection per agent in `mcp.config.json`. Each entry under `mcpServers`
has a `command`, `args`, and optional `env`. Use the
[timekeeper config](agents/timekeeper/mcp.config.json) as a local server example.
For no tools:

```json
{"mcpServers": {}}
```

`${ROOT_DIR}`, `${AGENT_DIR}`, and environment variables from `.env` and
`agent.env` are expanded in JSON string values. Put server-specific settings in
`agent.env` and credentials in the ignored root `.env`. Model changes leave
this tool configuration intact.

## Commands

```bash
./llm.sh list-agents
./llm.sh list-models
./llm.sh start <agent> [model]
./llm.sh restart <agent> [model]
./llm.sh stop
./llm.sh status
./llm.sh logs llama
./llm.sh logs mcp
```

Stop the Telegram bot before switching agents or models, wait for the new model
to load, then start the bot again. Prompt edits are picked up automatically.
Runtime config, logs, and PIDs live in the ignored `.state/` directory.

## Checks

```bash
python3 -m unittest discover -s tests -v
```

Bot checks are in [telegram-bot/README.md](telegram-bot/README.md).

[MIT License](LICENSE).
