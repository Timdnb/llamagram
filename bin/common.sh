#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$ROOT_DIR/.state"
PID_DIR="$STATE_DIR/pids"
LOG_DIR="$STATE_DIR/logs"
GENERATED_DIR="$STATE_DIR/generated"
AGENTS_DIR="$ROOT_DIR/agents"
ACTIVE_AGENT_FILE="$STATE_DIR/active-agent"
ACTIVE_MCP_CONFIG_FILE="$STATE_DIR/active-mcp-config"

LLAMA_PID="$PID_DIR/llama.pid"
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

write_active_agent() {
    local agent="$1"
    echo "$agent" > "$ACTIVE_AGENT_FILE"
    echo "$AGENT_RENDERED_MCP_CONFIG" > "$ACTIVE_MCP_CONFIG_FILE"
}

render_mcp_config() {
    AGENT_RENDERED_MCP_CONFIG="$GENERATED_DIR/${AGENT_NAME}-mcp.config.json"

    perl -pe 's/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/exists $ENV{$1} ? $ENV{$1} : $&/ge' \
        "$AGENT_MCP_CONFIG" > "$AGENT_RENDERED_MCP_CONFIG"

    if grep -Eq '\$\{[A-Za-z_][A-Za-z0-9_]*\}' "$AGENT_RENDERED_MCP_CONFIG"; then
        echo "Unresolved placeholders in: $AGENT_RENDERED_MCP_CONFIG"
        return 1
    fi

    export AGENT_RENDERED_MCP_CONFIG
}
