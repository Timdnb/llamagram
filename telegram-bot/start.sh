#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec pixi run --locked --manifest-path "$ROOT_DIR/pixi.toml" bot
