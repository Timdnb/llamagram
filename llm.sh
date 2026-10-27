#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${PIXI_WORKSPACE_ROOT:-${PIXI_PROJECT_ROOT:-}}" != "$ROOT_DIR" ]; then
    exec pixi run --locked --manifest-path "$ROOT_DIR/pixi.toml" bash "$ROOT_DIR/llm.sh" "$@"
fi

# shellcheck disable=SC1091
source "$ROOT_DIR/bin/common.sh"
# shellcheck disable=SC1091
source "$ROOT_DIR/bin/stack.sh"

usage() {
    cat <<EOF
llamagram Stack Controller

Usage:
  ./llm.sh list-agents
  ./llm.sh list-models
  ./llm.sh start <agent> [model]
  ./llm.sh stop
  ./llm.sh restart <agent> [model]
  ./llm.sh status
  ./llm.sh logs <llama|mcp>
EOF
}

guard_single_active_start() {
    if ! is_running "$LLAMA_PID" && ! is_running "$MCP_PROXY_PID"; then
        return 0
    fi

    local current_agent="unknown"
    local current_model="unknown"
    if [ -f "$ACTIVE_AGENT_FILE" ]; then current_agent="$(cat "$ACTIVE_AGENT_FILE")"; fi
    if [ -f "$ACTIVE_MODEL_FILE" ]; then current_model="$(cat "$ACTIVE_MODEL_FILE")"; fi
    if [ "$current_agent" != "$AGENT_NAME" ] || [ "$current_model" != "$MODEL_NAME" ]; then
        echo "Stack already running: agent=$current_agent model=$current_model"
        echo "Use './llm.sh restart $AGENT_NAME $MODEL_NAME' to switch."
        return 1
    fi
}

load_agent() {
    AGENT_NAME="$1"
    if [[ ! "$AGENT_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "Invalid agent name: $AGENT_NAME"
        return 1
    fi
    AGENT_DIR="$ROOT_DIR/agents/$AGENT_NAME"
    AGENT_MCP_CONFIG="$AGENT_DIR/mcp.config.json"

    local file
    for file in agent.env mcp.config.json system.md; do
        if [ ! -f "$AGENT_DIR/$file" ]; then
            echo "Missing agent file: $AGENT_DIR/$file"
            return 1
        fi
    done

    set -a
    # shellcheck disable=SC1090,SC1091
    source "$AGENT_DIR/agent.env"
    set +a
    MODEL_NAME="${2:-${AGENT_MODEL:-}}"
    if [[ ! "$MODEL_NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "Set AGENT_MODEL in agent.env, or pass a model name."
        return 1
    fi
    local model_config="$ROOT_DIR/models/$MODEL_NAME.env"
    if [ ! -f "$model_config" ]; then
        echo "Unknown model: $MODEL_NAME (see ./llm.sh list-models)"
        return 1
    fi

    # Model settings come exclusively from the selected model file.
    local key
    for key in "${!LLAMA_FLAG_@}"; do
        unset "$key"
    done
    LLAMA_MODEL=""
    set -a
    # shellcheck disable=SC1090
    source "$model_config"
    set +a
    if [ ! -f "$LLAMA_MODEL" ]; then
        echo "Model file not found: $LLAMA_MODEL"
        echo "Check the selected model and LLAMA_MODELS_DIR in .env."
        return 1
    fi
    LLAMA_ALIAS="$AGENT_NAME"
    export ROOT_DIR AGENT_DIR AGENT_NAME
    render_mcp_config
}

ensure_runtime_dirs
load_root_env
load_llama_defaults

command="${1:-help}"
arg="${2:-}"
model="${3:-}"

case "$command" in
    list-agents)
        list_agents
        ;;
    list-models)
        list_models
        ;;
    start)
        if [ -z "$arg" ]; then
            usage
            exit 1
        fi
        load_agent "$arg" "$model"
        guard_single_active_start
        start_llama
        start_mcp
        write_active_agent "$arg"
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
        # Validate the requested configuration before stopping a working stack.
        load_agent "$arg" "$model"
        stop_mcp
        stop_llama
        start_llama
        start_mcp
        write_active_agent "$arg"
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
        echo "Unknown command: $command"
        usage
        exit 1
        ;;
esac
