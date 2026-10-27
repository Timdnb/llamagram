#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT_DIR/.state"
PID_DIR="$STATE_DIR/pids"
LOG_DIR="$STATE_DIR/logs"
GENERATED_DIR="$STATE_DIR/generated"
AGENTS_DIR="$ROOT_DIR/agents"
ACTIVE_AGENT_FILE="$STATE_DIR/active-agent"
ACTIVE_MODEL_FILE="$STATE_DIR/active-model"
ACTIVE_MCP_CONFIG_FILE="$STATE_DIR/active-mcp-config"

# PID paths are consumed by stack.sh.
# shellcheck disable=SC2034
LLAMA_PID="$PID_DIR/llama.pid"
# shellcheck disable=SC2034
MCP_PROXY_PID="$PID_DIR/mcp-proxy.pid"

ensure_runtime_dirs() {
    mkdir -p "$STATE_DIR" "$PID_DIR" "$LOG_DIR" "$GENERATED_DIR"
}

load_root_env() {
    if [ -f "$ROOT_DIR/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        source "$ROOT_DIR/.env"
        set +a
    fi
}

load_llama_defaults() {
    if [ -f "$ROOT_DIR/infra/llama/defaults.env" ]; then
        # shellcheck disable=SC1091
        source "$ROOT_DIR/infra/llama/defaults.env"
    fi
}

mcp_config_has_servers() {
    local config_path="$1"

    python3 - "$config_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
servers = payload.get("mcpServers")
if not isinstance(servers, dict):
    raise SystemExit(2)
raise SystemExit(0 if any(isinstance(name, str) and name for name in servers.keys()) else 1)
PY
}

port_in_use() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk -v p=":$port" '$4 ~ (p "$") {found=1} END {exit found ? 0 : 1}'
        return $?
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi

    return 1
}

port_listener_summary() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ (p "$") {print; found=1} END {if (!found) exit 1}'
        return $?
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null
        return $?
    fi

    return 1
}

is_running() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

list_agents() {
    find "$AGENTS_DIR" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort
}

list_models() {
    find "$ROOT_DIR/models" -maxdepth 1 -name '*.env' -printf '%f\n' | sed 's/\.env$//' | sort
}

write_active_agent() {
    local agent="$1"
    echo "$agent" > "$ACTIVE_AGENT_FILE"
    echo "$MODEL_NAME" > "$ACTIVE_MODEL_FILE"
    echo "$AGENT_RENDERED_MCP_CONFIG" > "$ACTIVE_MCP_CONFIG_FILE"
}

render_mcp_config() {
    AGENT_RENDERED_MCP_CONFIG="$GENERATED_DIR/${AGENT_NAME}-mcp.config.json"

    python3 - "$AGENT_MCP_CONFIG" "$AGENT_RENDERED_MCP_CONFIG" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

def expand(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda match: os.environ[match[1]], value)
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    return value

config = expand(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
if not isinstance(config["mcpServers"], dict):
    raise ValueError("mcpServers must be an object")
Path(sys.argv[2]).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

    export AGENT_RENDERED_MCP_CONFIG
}
