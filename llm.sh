#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "$ROOT_DIR/bin/common.sh"
# shellcheck disable=SC1091
source "$ROOT_DIR/bin/stack.sh"

usage() {
    cat <<EOF
Llamagram Stack Controller

Usage:
  ./llm.sh list-agents
  ./llm.sh start <agent>
  ./llm.sh stop
  ./llm.sh restart <agent>
  ./llm.sh status
  ./llm.sh logs <llama|mcp>
EOF
}

guard_single_active_start() {
    local requested_agent="$1"

    local llama_running="false"
    local mcp_running="false"
    if is_running "$LLAMA_PID"; then llama_running="true"; fi
    if is_running "$MCP_PROXY_PID"; then mcp_running="true"; fi

    if [ "$llama_running" = "false" ] && [ "$mcp_running" = "false" ]; then
        return 0
    fi

    if [ ! -f "$ACTIVE_AGENT_FILE" ]; then
        echo "Stack services are running but active agent is unknown. Stop the stack first with ./llm.sh stop"
        return 1
    fi

    local current_agent
    current_agent="$(cat "$ACTIVE_AGENT_FILE")"
    if [ "$current_agent" != "$requested_agent" ]; then
        echo "Single active agent guard: stack is already running for '$current_agent'."
        echo "Use './llm.sh restart $requested_agent' or './llm.sh stop' first."
        return 1
    fi

    return 0
}

load_agent() {
    local agent="$1"
    local agent_dir="$ROOT_DIR/agents/$agent"

    if [ ! -d "$agent_dir" ]; then
        echo "Agent not found: $agent"
        return 1
    fi

    if [ ! -f "$agent_dir/agent.env" ]; then
        echo "Missing agent.env for $agent"
        return 1
    fi
    if [ ! -f "$agent_dir/mcp.config.json" ]; then
        echo "Missing mcp.config.json for $agent"
        return 1
    fi
    if [ ! -f "$agent_dir/system.md" ]; then
        echo "Missing system.md for $agent"
        return 1
    fi

    set -a
    # shellcheck disable=SC1090
    source "$agent_dir/agent.env"
    set +a

    local required=(
        AGENT_LLAMA_MODEL
        AGENT_LLAMA_ALIAS
        AGENT_MCP_LOCAL_TIMEZONE
    )

    local missing=()
    local key
    for key in "${required[@]}"; do
        if [ -z "${!key:-}" ]; then
            missing+=("$key")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Missing required keys in $agent_dir/agent.env: ${missing[*]}"
        return 1
    fi

    AGENT_NAME="$agent"
    AGENT_DIR="$agent_dir"
    AGENT_MCP_CONFIG="$agent_dir/mcp.config.json"

    LLAMA_MODEL="$AGENT_LLAMA_MODEL"
    LLAMA_ALIAS="$AGENT_LLAMA_ALIAS"
    export ROOT_DIR
    export AGENT_NAME
    export AGENT_MCP_LOCAL_TIMEZONE

    render_mcp_config
    write_active_agent "$agent"
}

ensure_runtime_dirs
load_root_env
load_llama_defaults

cmd="${1:-help}"
arg="${2:-}"

case "$cmd" in
    list-agents)
        list_agents
        ;;
    start)
        if [ -z "$arg" ]; then
            usage
            exit 1
        fi
        guard_single_active_start "$arg"
        load_agent "$arg"
        start_llama
        start_mcp
        ;;
    stop)
        stop_mcp
        stop_llama
        ;;
    restart)
        if [ -z "$arg" ]; then
            usage
            exit 1
        fi
        stop_mcp || true
        stop_llama || true
        load_agent "$arg"
        start_llama
        start_mcp
        ;;
    status)
        show_status
        ;;
    logs)
        if [ -z "$arg" ]; then
            usage
            exit 1
        fi
        show_logs "$arg"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $cmd"
        usage
        exit 1
        ;;
esac
