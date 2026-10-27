---
name: llamagram-setup
description: Create or update a local llamagram agent from a natural-language request, configuring its model, system prompt, MCP tools, optional memory, and Telegram capabilities. Use for agent setup and model/tool changes in a llamagram checkout.
---

# Set up a llamagram agent

Turn the user's request into a usable local agent. Work from the llamagram
repository root; all paths below are relative to that root. This skill configures
the local agent, not the coding assistant's own tools or system prompt.

## Establish the setup

Read `README.md`, `pixi.toml`, the relevant starter agent, and the selected model
file. Check existing configuration before changing it. Inspect `llm.sh`, `bin/`, or
`telegram-bot/README.md` when their behavior matters; prefer the checkout's actual
contract if it has changed since this skill was written.

Identify the agent's purpose, response style, needed data/actions, model, and
whether it needs images or persistent notes. Use preferences already supplied.
Ask only for material missing choices, such as which service/account to connect,
whether actions should be read-only, or hardware limits when choosing a new model.
State reasonable defaults and continue with independent configuration work.

Reuse installed models and MCP servers when they fit. If a download is needed,
check the exact artifact, size, destination, free space, and runtime compatibility.
Resolve an unclear model choice before a large download; preserve authorization
already given. Use upstream documentation for external servers and runtime flags.
Do not treat a model listed in `models/` as proof its weights are installed.

## Keep configuration modular

| Path | Put here |
|---|---|
| `models/<model>.env` | `LLAMA_MODEL` and `LLAMA_FLAG_*` inference settings |
| `agents/<agent>/agent.env` | `AGENT_MODEL` and agent-specific MCP settings |
| `agents/<agent>/mcp.config.json` | Enabled MCP servers and their launch configuration |
| `agents/<agent>/system.md` | The local agent's instructions |
| `.env` | Machine paths, timezone, and service credentials; ignored by Git |
| `telegram-bot/.env` | Telegram token, access lists, attachment limits, and image opt-in |

Use a simple lowercase hyphenated agent name. Create the three required agent
files even when the prompt is short or no tools are needed. Default new personal
agent directories to Git-ignored; publish a reusable example only when requested.
Keep personal prompts and notes out of public documentation and commits.

Keep shared profiles reusable. To customize inference settings for one agent,
create a named model variant rather than changing a shared profile unexpectedly
or putting inference flags back into `agent.env`. A model variant can source a
base file using `${ROOT_DIR}/models/<base>.env` and override selected settings.

Weights belong under `LLAMA_MODELS_DIR`, outside the repository. Use that variable
in model files. Keep profiles to relevant settings, without machine-specific
comments. The controller owns model alias and port. `LLAMA_FLAG_CTX_SIZE` becomes
`--ctx-size`; `true` adds a switch, `false` omits it, and other values—including
`0`, `1`, `on`, and `off`—are passed literally.

## Configure tools and memory

Select MCPs per agent in `mcp.config.json`, not in model profiles or the coding
assistant's global MCP settings. The supported local shape is:

```json
{
  "mcpServers": {
    "service-name": {
      "command": "server-executable",
      "args": ["server-argument"],
      "env": {"SERVICE_TOKEN": "${SERVICE_TOKEN}"}
    }
  }
}
```

For no tools use `{"mcpServers": {}}`. `${ROOT_DIR}`, `${AGENT_DIR}`, and exported
variables from `.env`, `agent.env`, and the model file expand inside JSON string
values. Define every placeholder. Preserve existing `.env` entries; do not put
credential values in tracked JSON, prompts, command examples, or logs.

The controller and bot run in the locked Pixi environment. Use `python` for
bundled servers; reuse `agents/timekeeper/mcp.config.json` for local time tooling.
Add Python server dependencies with `pixi add --pypi <package>` and commit both
`pixi.toml` and `pixi.lock`. External servers may use their documented runtime.
For external MCPs, verify their launch command, authentication steps, tool names,
and filtering options. The current proxy configuration launches stdio servers; a
remote HTTP MCP may need a supported stdio bridge. Do not invent a URL-based
config shape or unsupported per-tool filters. Leave interactive account login to
the user when it cannot be completed through their authorized session.

If persistent notes are requested, use `mcps/markdown/server.py` with
`--target=${AGENT_MCP_MARKDOWN_TARGET}`. Set that variable in `agent.env`, for
example `AGENT_MCP_MARKDOWN_TARGET="${AGENT_DIR}/notes.md"`, and create the file
before starting its server. Scope access to the requested file or directory.
Tell the prompt when to read and update these notes. Conversation history alone
is in memory and disappears on reset/restart. Keep personal memory files ignored,
including when an otherwise reusable agent template is tracked.

The Telegram bridge exposes names as `<server>__<tool>`, such as
`time__get_current_time` and `markdown__read_markdown`. Use verified exposed names
when naming tools in the prompt. Account for schemas, history, tool results,
reasoning, and image tokens when selecting context size. A large tool catalog
can consume the context before the user sends a message; use documented server
filters where appropriate.

## Write the prompt and match capabilities

Write a focused `system.md` covering the requested role, useful context, tone,
output preferences, and when its configured tools or notes should be used.
Ground requested live-data answers in those tools and handle unavailable data
honestly. A prompt cannot grant tools, persistent storage, scheduling, audio, or
background execution that the stack does not implement. Identify any requested
capability that requires additional implementation instead of claiming it works.

For vision, select a model profile with the matching family's
`LLAMA_FLAG_MMPROJ` projector and verify that file exists. Telegram also needs
`IMAGE_SUPPORT_ENABLED=true`. A loaded text-only model is not made vision-capable
by that bot setting: check the running server's `/props` → `modalities.vision`
and the bot's `/images` result. Check llama.cpp compatibility for newer GGUFs;
missing tensors can indicate an old runtime, not a failed download. In particular,
the included Qwen3.8 profiles were verified with build b11669.

`system.md` is injected by the Telegram bridge and re-read on each model call;
it does not configure the llama-server web UI's system prompt.

## Validate and run within the request

Check Bash syntax for edited env files, parse MCP JSON, resolve model/projector
paths, and verify required variables and memory targets. For code changes, run
`pixi run check` (lint, formatting, and tests). Use `pixi run format` to format
Python changes. For configuration-only changes, validate the resulting
configuration; avoid adding tests that restate file text.

If running the agent is in scope, inspect `./llm.sh status` first. Use:

```text
./llm.sh start <agent> [model]
./llm.sh restart <agent> [model]
./llm.sh logs llama
./llm.sh logs mcp
```

Omitting the model uses `AGENT_MODEL`; specifying one replaces model settings
while preserving the agent's prompt and tools. Do not stop another running stack
when the user only asked to prepare configuration. Stop the Telegram bot before
switching agents/models, wait for the model to finish loading, then run
`./start.sh` from `telegram-bot/`. A PID or startup message alone does not prove
readiness: check the configured server's `/health` and test a short reply.

Verify the actual MCP tool list and an appropriate read-only call before claiming
a service integration works. Exercise image input when vision is requested and
runtime testing is authorized. Avoid test actions that write to external services
or send real Telegram messages unless they are part of the user's request.

Report the created/changed files, chosen model, configured tools, exact start and
switch-back commands, and any remaining login or installation step. Distinguish
configuration checks from live inference/tool tests. Follow the user's existing
Git instructions; preparing a personal agent does not itself imply publishing it.
