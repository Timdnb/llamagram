---
name: llamagram-setup
description: Set up a custom local llamagram agent, from cloning the repo and preparing llama-server to choosing an Unsloth GGUF for the user's hardware, configuring MCP capabilities, and creating agent files with launch instructions. Use for first installations and existing agent, model, or tool changes.
---

# Set up a custom llamagram agent

Turn the user's request into a usable local agent through the four stages below.
Start with the existing installation when available; skip completed stages rather
than rebuilding or downloading unnecessarily. This skill sets up the local agent;
its MCP tools and prompt belong to llamagram, not the coding assistant.

Establish the intended role, response style, required services/data, image input,
persistent notes, and preference for speed versus quality. Use information already
supplied. Inspect what is available locally before asking for missing details;
ask only when a choice materially affects the result. A setup request authorizes
setup work, not unrelated external actions or publishing personal configuration.

## 1. Prepare the installation and paths

For a fresh installation, clone [llamagram](https://github.com/Timdnb/llamagram)
into the user's chosen location. Reuse an existing checkout without overwriting
local changes. Read its `README.md`, `pixi.toml`, and relevant starter files;
inspect `llm.sh`, `bin/`, and `telegram-bot/` when behavior matters. The checkout's
actual contract takes precedence over stale examples in this skill.

The current checkout targets Linux x86-64 and Bash 4.3+. Check the OS and
architecture; explain any unsupported platform work before promising a working
installation. Install or reuse [Pixi](https://pixi.sh), then run
`pixi install --locked` from the checkout. Pixi supplies Python and the bot/MCP
dependencies. A llama.cpp build also needs its native compiler, CMake, and the
selected backend's prerequisites; Pixi's Python environment alone is insufficient.

Reuse a compatible `llama-server` if installed. Otherwise obtain
[llama.cpp](https://github.com/ggml-org/llama.cpp) outside the llamagram checkout
and follow its current [build instructions](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).
Choose the backend for the detected GPU, or CPU inference when appropriate.
For NVIDIA, check the driver and CUDA toolkit separately; `nvidia-smi` alone does
not prove that the CUDA compiler is installed. After choosing `llama_cpp_dir`,
a typical NVIDIA source build is:

```bash
cmake -S "$llama_cpp_dir" -B "$llama_cpp_dir/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON
cmake --build "$llama_cpp_dir/build" --config Release --target llama-server -j 2
```

Adjust build parallelism to the CPU and available RAM. For another backend, use
its documented options instead of reusing CUDA flags. Verify the resulting
binary with `--version` and `--help`, including its runtime library requirements.
Record its version/commit and recheck model compatibility in stage 2; preserve a
working runtime when a different build is needed for a new model.

Create root `.env` from `.env.example` only if absent; merge needed values into
an existing file. Set `LLAMA_SERVER_BIN` to the executable's absolute path and
`LLAMA_MODELS_DIR` to a writable weights directory outside the checkout. Quote
paths containing spaces. Set `LOCAL_TIMEZONE` to the user's IANA timezone, or
use UTC when no preference is supplied. Keep machine paths and credentials in
ignored env files. Do not replace the templates with one user's paths.

## 2. Select and obtain a model for the hardware

Inventory the actual machine, including currently available resources:

| Resource | Establish |
|---|---|
| GPU | Vendor, model, total/free VRAM, supported inference backend |
| CPU | Model, cores, and likely CPU inference/offload constraints |
| RAM | Installed and currently available host memory |
| Storage | SSD or HDD for the weights, free space on that filesystem |

On Linux, useful read-only checks include `lscpu`, `free -h`, `lsblk`, and
`df -h` for the chosen model destination. For NVIDIA, use
`nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader`.
If a detail cannot be detected reliably, ask rather than inferring it from
another user's machine or this repository's existing profiles.

Prefer [Unsloth GGUFs](https://huggingface.co/unsloth) for new downloads. Consult
its [model catalog](https://unsloth.ai/docs/get-started/unsloth-model-catalog),
the chosen model's current Unsloth guide, Hugging Face model card, and exact
file listing. Select an instruction/chat model with the required tool calling,
language, reasoning, and vision support. Choose GGUF artifacts for llama.cpp;
other formats in Unsloth's catalog are not interchangeable.

Compare model size and quantization together. Estimate a memory budget for
resident weights, context/KV cache or architecture-specific state, compute
buffers, and an image projector if needed, leaving room for the OS and desktop.
Account for MCP schemas, history, tool results, reasoning, and images when
choosing context length. GGUF file size is not total runtime memory; MoE active
parameter count is not the size of all its weights.

Favor a configuration with useful memory headroom and responsive interaction.
When weights need CPU/RAM offload, explain the likely speed cost and CPU/memory
constraints; do not treat free RAM as equivalent to VRAM. SSD storage helps
loading, while insufficient memory and disk paging can make interaction slow,
especially on HDDs. Disk capacity is not a substitute for RAM or VRAM. Avoid
unsupported tokens-per-second promises; label estimates and measure when able.

Recommend one model/quantization and, when useful, one smaller/faster or
higher-quality alternative. Explain why the recommendation fits the hardware
and purpose, with source links and expected tradeoffs. Respect an already
selected model; surface a concrete fit or compatibility problem if present.
Resolve material ambiguity before a large download without asking again for
choices or downloads the user already authorized.

Before downloading, verify the exact repo, revision, filenames, total bytes,
free destination space, and llama.cpp support for that architecture and GGUF
variant. Include all shards for the selected quantization and its matching
vision projector when required; do not download every quantization in the repo.
Use a resumable downloader with explicit file selection. Obtain download tools
through Pixi when needed rather than adding another project dependency manager.
Keep upstream filenames under a consistent layout such as:

```text
<LLAMA_MODELS_DIR>/<family>/<variant>/<upstream-filename>.gguf
```

Check completion against upstream sizes/checksums when available. Configure a
sharded model using the documented entry shard. A profile in `models/` is not
proof its weights are installed. Verify model-specific runtime flags and chat
template settings against upstream guidance and the installed server's help.
Missing tensors can mean an incompatible runtime; investigate before assuming
the download is corrupt or replacing a working runtime.

## 3. Select the agent's capabilities

Translate the requested abilities into the smallest useful set of MCP servers.
Reuse bundled time tooling or scoped Markdown notes when they fit. Select MCPs
per agent so switching models preserves its tools and prompt. Tool calling is
a model requirement; installing an MCP does not make an unsuitable model good
at using tools. Revise stage 2 if the selected capabilities change its needs.

For external MCPs, verify their maintained launch command, dependencies,
authentication, exposed tools, and supported filtering options. The proxy
launches stdio servers; a remote HTTP MCP may need a documented stdio bridge.
Do not invent a URL-based config shape or unsupported per-tool filters. Scope
file/account access to the request and honor read-only preferences. Complete
available setup work and identify any login that requires the user.

Bundled servers run with `python` in the locked Pixi environment. Add a Python
server package with `pixi add --pypi <package>` when appropriate, keeping the
manifest and lockfile together. External servers can use their documented
runtime. If required authentication or tooling cannot be completed, report the
remaining step rather than claiming the capability works.

For notes, use `mcps/markdown/server.py` with
`--target=${AGENT_MCP_MARKDOWN_TARGET}`. Set that variable in `agent.env`, for
example `AGENT_MCP_MARKDOWN_TARGET="${AGENT_DIR}/notes.md"`, and create the file.
Scope it to the requested file/directory. Instruct the agent when to read and
update notes. Chat history is in memory and disappears on reset/restart.

For vision, obtain the selected family's compatible projector and set
`LLAMA_FLAG_MMPROJ` in a vision model profile. Telegram also requires
`IMAGE_SUPPORT_ENABLED=true`; confirm the loaded server's `/props` reports
`modalities.vision` and check `/images`. A bot setting cannot add model vision.
A prompt likewise cannot provide scheduling, background execution, audio, or
other behavior that the stack does not implement; identify additional work.

## 4. Write the configuration and hand over a runnable agent

Use the choices above to create or update these files, relative to the checkout:

| Path | Contents |
|---|---|
| `.env` | Absolute runtime/model-root paths, timezone, service credentials; ignored |
| `models/<model>.env` | `LLAMA_MODEL` and `LLAMA_FLAG_*` inference settings |
| `agents/<agent>/agent.env` | `AGENT_MODEL` and agent-specific `AGENT_MCP_*` settings |
| `agents/<agent>/mcp.config.json` | Enabled MCP servers and their launch configuration |
| `agents/<agent>/system.md` | Role, tone, context, and instructions for the configured tools |
| `telegram-bot/.env` | Telegram token, user/chat allowlists, image opt-in, limits; ignored |

Use a lowercase hyphenated agent name. Create all three required agent files,
even for a short prompt or no tools. Default new personal agent directories and
notes to Git-ignored. Publish reusable examples only when requested; keep
personal prompts, memory, and credentials out of public docs and commits.

In model profiles, use `LLAMA_MODEL="${LLAMA_MODELS_DIR}/..."` and relevant
flags without machine-specific comments. `LLAMA_FLAG_CTX_SIZE` becomes
`--ctx-size`; `true` adds a switch, `false` omits it, and other values, including
`0`, `1`, `on`, and `off`, are passed literally. The controller owns alias and
port. Keep shared profiles reusable: create a named variant for agent-specific
inference settings. A variant can source `${ROOT_DIR}/models/<base>.env` and
override selected values; a vision variant can add its projector this way.

Use this supported MCP config shape, or `{"mcpServers": {}}` for no tools:

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

Reuse `agents/timekeeper/mcp.config.json` as a bundled server example.
`${ROOT_DIR}`, `${AGENT_DIR}`, and exported env variables expand inside JSON
string values. Define every placeholder; keep credentials in root `.env`.
Generated configs, logs, and PIDs belong in `.state/`; let the controller write
them. Configure the agent's MCPs here rather than in the coding assistant's
own global settings.

Write a focused prompt matching the chosen capabilities. Exposed Telegram tool
names are `<server>__<tool>`, such as `time__get_current_time`; verify names before
using them in the prompt. Ground live-data answers in those tools. `system.md`
is injected and re-read by the Telegram bot; it does not set the server web UI's
system prompt. For Telegram, create its `.env` from its example if absent and
set the token and allowed user IDs; never guess access IDs or expose the token.

Validate edited env files with Bash syntax checks, parse MCP JSON, and verify
expanded paths, executables, variables, weights/projector files, and note targets.
Use `git check-ignore` to confirm personal files and secrets stay ignored. For
code changes run `pixi run check`; use `pixi run format` for Python formatting.
Configuration-only work needs configuration validation, not tests matching text.

Always give launch instructions with the actual chosen agent/model names:

```text
./llm.sh status
./llm.sh start <agent>
./llm.sh logs llama
./llm.sh logs mcp
pixi run --locked bot
```

Run them when launching is in scope. Only one agent stack runs at a time;
inspect status and preserve the previous selection before changing it. Stop
the bot with Ctrl+C before a switch, use
`./llm.sh restart <agent> [model]`, wait for readiness, then restart the bot.
Omitting the model restores that agent's `AGENT_MODEL` default. Include the
exact command to restore a previous selection when replacing an existing setup,
and `./llm.sh stop` to stop the stack.

A startup PID alone is not readiness. Check `/health` on the configured llama
port, try a short model response, list actual MCP tools and make an appropriate
read-only call. Test image input when requested and running tests is in scope.
Avoid sending real Telegram messages or writing to external services unless
requested. If runtime access is unavailable, distinguish validated configuration
from untested inference or service integration.

Finish with the selected model/quantization and hardware rationale, configured
capabilities, created file paths, validation results, launch/switch-back commands,
and any remaining login or installation step. Preparing an agent does not itself
imply committing or publishing it; follow the user's existing Git instructions.
