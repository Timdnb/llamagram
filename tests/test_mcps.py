"""Exercise the bundled servers over MCP without a running model or proxy."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_server_converts_across_daylight_saving(self):
        server = StdioServerParameters(
            command=sys.executable,
            args=[
                str(ROOT / "mcps/time/server.py"),
                "--local-timezone=Europe/Amsterdam",
            ],
        )
        async with stdio_client(server) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=10)
            ) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertIn("convert_time", {tool.name for tool in tools.tools})
                for date, expected_hour in (("2026-01-15", "11"), ("2026-07-15", "10")):
                    with self.subTest(date=date):
                        result = await session.call_tool(
                            "convert_time",
                            {"time": f"{date}T12:00:00", "target_timezone": "UTC"},
                        )
                        self.assertFalse(result.isError, result.content)
                        self.assertEqual(
                            result.structuredContent["target_time"],
                            f"{date}T{expected_hour}:00:00+00:00",
                        )

    async def test_markdown_server_edits_only_the_selected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = Path(directory) / "notes.md"
            notes.touch()
            other = Path(directory) / "other.md"
            other.write_text("Keep this", encoding="utf-8")
            server = StdioServerParameters(
                command=sys.executable,
                args=[str(ROOT / "mcps/markdown/server.py"), f"--target={notes}"],
            )
            async with stdio_client(server) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "write_markdown", {"path": None, "content": "# Notes\n"}
                    )
                    self.assertFalse(result.isError, result.content)
                    result = await session.call_tool("read_markdown", {})
                    self.assertFalse(result.isError, result.content)
                    self.assertEqual(result.structuredContent["content"], "# Notes\n")
                    for tool, arguments in (
                        ("read_markdown", {"path": str(other)}),
                        ("write_markdown", {"path": "other.md", "content": "Changed"}),
                        ("delete_markdown", {"path": "../outside.md"}),
                    ):
                        with self.subTest(tool=tool):
                            result = await session.call_tool(tool, arguments)
                            self.assertTrue(result.isError, result.content)
            self.assertEqual(notes.read_text(encoding="utf-8"), "# Notes\n")
            self.assertEqual(other.read_text(encoding="utf-8"), "Keep this")
