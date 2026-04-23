#!/usr/bin/env bash

set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

append_llama_agent_flags() {
    local -n _cmd_ref="$1"
    local name value suffix flag

    while IFS='=' read -r name value; do
        case "$name" in
            AGENT_LLAMA_FLAG_*)
                suffix="${name#AGENT_LLAMA_FLAG_}"
                flag="$(printf '%s' "$suffix" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"

                # Core startup flags are controlled by dedicated required keys.
                if [ "$flag" = "model" ] || [ "$flag" = "alias" ] || [ "$flag" = "port" ]; then
                    continue
                fi

                if [ -z "$value" ]; then
                    continue
                fi

                case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
                    true|1|yes|on)
                        _cmd_ref+=(--"$flag")
                        ;;
                    false|0|no|off)
                        ;;
                    *)
                        _cmd_ref+=(--"$flag" "$value")
                        ;;
                esac
                ;;
        esac
    done < <(env)
}

start_llama() {
    if is_running "$LLAMA_PID"; then
        echo "llama-server already running (pid=$(cat "$LLAMA_PID"))"
        return 0
    fi

    if [ ! -x "${LLAMA_SERVER_BIN:-}" ]; then
        echo "llama-server binary not found or not executable: ${LLAMA_SERVER_BIN:-unset}"
        return 1
    fi

    local cmd=(
        "$LLAMA_SERVER_BIN"
        --model "$LLAMA_MODEL"
        --alias "$LLAMA_ALIAS"
        --port "$LLAMA_PORT"
    )

    append_llama_agent_flags cmd

    nohup "${cmd[@]}" > "$LOG_DIR/llama.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$LLAMA_PID"
    sleep 2

    if is_running "$LLAMA_PID"; then
        echo "llama-server started (pid=$pid, port=$LLAMA_PORT)"
        echo "model=$LLAMA_MODEL"
        echo "alias=$LLAMA_ALIAS"
        return 0
    fi

    echo "failed to start llama-server"
    tail -n 80 "$LOG_DIR/llama.log" || true
    return 1
}

stop_llama() {
    if ! is_running "$LLAMA_PID"; then
        echo "llama-server not running"
        return 0
    fi
    local pid
    pid=$(cat "$LLAMA_PID")
    kill "$pid" 2>/dev/null || true
    sleep 1
    if is_running "$LLAMA_PID"; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$LLAMA_PID"
    echo "llama-server stopped"
}

start_mcp() {
    if is_running "$MCP_PROXY_PID"; then
        echo "mcp-proxy already running (pid=$(cat "$MCP_PROXY_PID"))"
        return 0
    fi

    if [ ! -f "$AGENT_RENDERED_MCP_CONFIG" ]; then
        echo "rendered mcp config missing: $AGENT_RENDERED_MCP_CONFIG"
        return 1
    fi

    nohup uvx mcp-proxy \
        --named-server-config "$AGENT_RENDERED_MCP_CONFIG" \
        --allow-origin '*' \
        --port "$MCP_PROXY_PORT" \
        --stateless \
        > "$LOG_DIR/mcp.log" 2>&1 &

    local pid=$!
    echo "$pid" > "$MCP_PROXY_PID"
    sleep 2

    if is_running "$MCP_PROXY_PID"; then
        echo "mcp-proxy started (pid=$pid, port=$MCP_PROXY_PORT)"
        return 0
    fi

    echo "failed to start mcp-proxy"
    tail -n 80 "$LOG_DIR/mcp.log" || true
    return 1
}

stop_mcp() {
    if ! is_running "$MCP_PROXY_PID"; then
        echo "mcp-proxy not running"
        return 0
    fi
    local pid
    pid=$(cat "$MCP_PROXY_PID")
    kill "$pid" 2>/dev/null || true
    sleep 1
    if is_running "$MCP_PROXY_PID"; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$MCP_PROXY_PID"
    echo "mcp-proxy stopped"
}

show_status() {
    if is_running "$LLAMA_PID"; then
        echo "llama-server: running (pid=$(cat "$LLAMA_PID"), port=${LLAMA_PORT:-unknown})"
    else
        echo "llama-server: stopped"
    fi

    if is_running "$MCP_PROXY_PID"; then
        echo "mcp-proxy: running (pid=$(cat "$MCP_PROXY_PID"), port=${MCP_PROXY_PORT:-unknown})"
    else
        echo "mcp-proxy: stopped"
    fi

    if [ -f "$ACTIVE_AGENT_FILE" ]; then
        echo "active-agent: $(cat "$ACTIVE_AGENT_FILE")"
    else
        echo "active-agent: none"
    fi

    if [ -f "$ACTIVE_MCP_CONFIG_FILE" ]; then
        echo "active-mcp-config: $(cat "$ACTIVE_MCP_CONFIG_FILE")"
    fi
}

show_logs() {
    local service="$1"
    case "$service" in
        llama)
            tail -n 120 "$LOG_DIR/llama.log" || true
            ;;
        mcp)
            tail -n 120 "$LOG_DIR/mcp.log" || true
            ;;
        *)
            echo "Unknown service: $service"
            return 1
            ;;
    esac
}
