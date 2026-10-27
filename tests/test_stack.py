"""Exercise controller configuration without starting a model or MCP processes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy(ROOT / "llm.sh", self.root / "llm.sh")
        for folder in ("bin", "models"):
            shutil.copytree(ROOT / folder, self.root / folder)
        agent = self.root / "agents/test"
        agent.mkdir(parents=True)
        (agent / "agent.env").write_text("AGENT_MODEL=original-vision\n")
        (agent / "mcp.config.json").write_text('{"mcpServers": {}}')
        (agent / "system.md").write_text("Be helpful")
        self.models = self.root / "weights"
        self.models.mkdir()
        self.original = self.models / "original.gguf"
        self.original.touch()
        (self.models / "projector.gguf").touch()
        (self.root / "models/original.env").write_text(
            'LLAMA_MODEL="${LLAMA_MODELS_DIR}/original.gguf"\n'
            "LLAMA_FLAG_CTX_SIZE=16384\n"
        )
        (self.root / "models/original-vision.env").write_text(
            'source "${ROOT_DIR}/models/original.env"\n'
            'LLAMA_FLAG_MMPROJ="${LLAMA_MODELS_DIR}/projector.gguf"\n'
        )
        path = self.models / "Qwen3.8/27B"
        path.mkdir(parents=True)
        (path / "Qwen3.8-27B-UD-IQ3_XXS.gguf").touch()
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("AGENT_", "LLAMA_"))
        }
        self.env["LLAMA_MODELS_DIR"] = str(self.models)
        self.env["PIXI_WORKSPACE_ROOT"] = str(self.root)
        (self.root / "infra/llama").mkdir(parents=True)
        shutil.copy(
            ROOT / "infra/llama/defaults.env", self.root / "infra/llama/defaults.env"
        )
        # Preserve the real flag builder; replace only process lifecycle functions.
        with (self.root / "bin/stack.sh").open("a") as handle:
            handle.write("""
start_llama() {
    local cmd=(--model "$LLAMA_MODEL" --alias "$LLAMA_ALIAS")
    append_llama_flags cmd
    printf '%s\\n' "${cmd[@]}" > "$ROOT_DIR/arguments"
}
start_mcp() { :; }
stop_mcp() { echo mcp >> "$ROOT_DIR/stopped"; }
stop_llama() { echo llama >> "$ROOT_DIR/stopped"; }
""")

    def run_stack(self, *args):
        return subprocess.run(
            ["bash", str(self.root / "llm.sh"), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_model_override_replaces_flags_and_preserves_agent_alias(self):
        result = self.run_stack("start", "test", "qwen38-iq3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "arguments").read_text().splitlines()
        self.assertEqual(args[args.index("--alias") + 1], "test")
        self.assertEqual(args[args.index("--ctx-size") + 1], "32768")
        self.assertEqual(args[args.index("--parallel") + 1], "1")
        self.assertEqual(args[args.index("--min-p") + 1], "0.0")
        self.assertEqual(args[args.index("--n-gpu-layers") + 1], "auto")
        self.assertEqual(
            args[args.index("--chat-template-kwargs") + 1], '{"reasoning_effort":"low"}'
        )
        self.assertIn("--jinja", args)
        self.assertNotIn("--mmproj", args)
        self.assertEqual(
            (self.root / ".state/active-model").read_text().strip(), "qwen38-iq3"
        )

    def test_omitting_model_restores_agent_default(self):
        self.run_stack("start", "test", "qwen38-iq3")
        result = self.run_stack("restart", "test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "arguments").read_text().splitlines()
        self.assertEqual(args[args.index("--model") + 1], str(self.original))
        self.assertEqual(args[args.index("--ctx-size") + 1], "16384")
        self.assertIn("--mmproj", args)
        self.assertNotIn("--chat-template-kwargs", args)

    def test_missing_download_does_not_stop_stack_or_replace_selection(self):
        self.run_stack("start", "test")
        result = self.run_stack("restart", "test", "qwen38-q4")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Model file not found", result.stdout)
        self.assertFalse((self.root / "stopped").exists())
        self.assertEqual(
            (self.root / ".state/active-model").read_text().strip(), "original-vision"
        )

    def test_unknown_models_and_path_traversal_fail_before_stopping(self):
        for name in ("unknown", "../outside"):
            result = self.run_stack("restart", "test", name)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / "stopped").exists())

    def test_start_with_another_model_requires_restart(self):
        self.run_stack("start", "test")
        pid = self.root / ".state/pids/llama.pid"
        pid.write_text(str(os.getpid()))
        result = self.run_stack("start", "test", "qwen38-iq3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Stack already running", result.stdout)

    def test_numeric_zero_and_on_are_values_and_false_omits_switch(self):
        with (self.root / "models/original.env").open("a") as handle:
            handle.write(
                "LLAMA_FLAG_SEED=0\nLLAMA_FLAG_FLASH_ATTN=on\nLLAMA_FLAG_JINJA=false\n"
            )
        result = self.run_stack("start", "test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = (self.root / "arguments").read_text().splitlines()
        self.assertEqual(args[args.index("--seed") + 1], "0")
        self.assertEqual(args[args.index("--flash-attn") + 1], "on")
        self.assertNotIn("--jinja", args)

    def test_model_switch_preserves_mcp_settings_and_escapes_json_values(self):
        setting = 'quoted "value" with a backslash \\ and space'
        self.env["TOOL_SETTING"] = setting
        agent = self.root / "agents/test"
        (agent / "mcp.config.json").write_text("""{
            "mcpServers": {"example": {
                "command": "python3",
                "args": ["${AGENT_DIR}/server.py", "${TOOL_SETTING}"],
                "env": {"SETTING": "${TOOL_SETTING}"}
            }}
        }""")
        rendered = self.root / ".state/generated/test-mcp.config.json"
        self.assertEqual(self.run_stack("start", "test").returncode, 0)
        original = json.loads(rendered.read_text())
        self.assertEqual(
            original["mcpServers"]["example"]["args"],
            [str(agent / "server.py"), setting],
        )
        result = self.run_stack("restart", "test", "qwen38-iq3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(rendered.read_text()), original)

    def test_missing_mcp_variable_fails_before_stopping(self):
        (self.root / "agents/test/mcp.config.json").write_text(
            '{"mcpServers": {"example": {"command": "${UNSET_TOOL_COMMAND}"}}}'
        )
        result = self.run_stack("restart", "test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNSET_TOOL_COMMAND", result.stderr)
        self.assertFalse((self.root / "stopped").exists())

    def test_explicit_model_works_without_an_agent_default(self):
        (self.root / "agents/test/agent.env").write_text("")
        self.assertNotEqual(self.run_stack("start", "test").returncode, 0)
        result = self.run_stack("start", "test", "qwen38-iq3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
