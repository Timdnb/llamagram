from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from telegram.error import BadRequest

from bot import TelegramBot
from chat import ChatClient, trim_history
from config import BotConfig
from formatting import reply, strip_reasoning, telegram_html


def config(prompt: Path, **changes) -> BotConfig:
    base = BotConfig(
        token="test-token",
        allowed_user_ids=frozenset({1}),
        allowed_chat_ids=frozenset(),
        agent_name="test",
        system_prompt_path=prompt,
        llama_url="http://llama",
        mcp_url="http://mcp",
        mcp_servers=(),
        min_seconds_between_messages=0,
    )
    return replace(base, **changes)


class ConfigTests(unittest.TestCase):
    def test_loads_active_agent_and_honors_port_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            for folder in ("telegram-bot", "infra/llama", ".state", "agents/test"):
                (root / folder).mkdir(parents=True)
            (root / "telegram-bot/.env").write_text(
                "TELEGRAM_BOT_TOKEN=test\nTELEGRAM_ALLOWED_USER_IDS=1, 2\n"
            )
            (root / ".env").write_text("LLAMA_PORT=9000\n")
            (root / "infra/llama/defaults.env").write_text(
                "LLAMA_PORT=${LLAMA_PORT:-8002}\nMCP_PROXY_PORT=${MCP_PROXY_PORT:-8001}\n"
            )
            (root / ".state/active-agent").write_text("test\n")
            (root / ".state/active-mcp-config").write_text(".state/tools.json\n")
            (root / ".state/tools.json").write_text('{"mcpServers": {"time": {}}}')
            (root / "agents/test/system.md").write_text("Be helpful")
            loaded = BotConfig.load(root)
            self.assertEqual(loaded.llama_url, "http://127.0.0.1:9000")
            self.assertEqual(loaded.mcp_url, "http://127.0.0.1:8001")
            self.assertEqual(loaded.mcp_servers, ("time",))
            self.assertEqual(loaded.allowed_user_ids, {1, 2})


class FormattingTests(unittest.IsolatedAsyncioTestCase):
    def test_code_and_identifiers_are_not_emphasized(self):
        self.assertEqual(
            telegram_html("Use `my_variable_name` and my_variable_name"),
            "Use <code>my_variable_name</code> and my_variable_name",
        )

    def test_headings_emphasis_and_code_are_escaped(self):
        self.assertEqual(telegram_html("# <Title>\n**bold** and _italic_"),
                         "<b>&lt;Title&gt;</b>\n<b>bold</b> and <i>italic</i>")
        rendered = telegram_html('```python\nx = "<a>"\n```\nDone')
        self.assertIn('<pre><code>x = &quot;&lt;a&gt;&quot;\n</code></pre>', rendered)
        self.assertTrue(rendered.endswith("Done"))

    def test_reasoning_only_and_unclosed_reasoning_are_hidden(self):
        self.assertEqual(strip_reasoning("<think>private</think>Answer"), "Answer")
        self.assertEqual(strip_reasoning("<think>private</think>"), "")
        self.assertEqual(strip_reasoning("<think>unfinished"), "")

    async def test_plain_commands_do_not_interpret_html(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        await reply(message, "Use <agent>")
        message.reply_text.assert_awaited_once_with("Use <agent>", parse_mode=None)

    async def test_long_reply_chunks_preserve_all_text_and_unicode(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        text = "😀" * 4500
        await reply(message, text, markdown=True)
        chunks = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 < 4096 for chunk in chunks))

    async def test_parse_error_falls_back_to_original_text(self):
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=[BadRequest("Can't parse entities"), None]))
        await reply(message, "**bold**", markdown=True)
        self.assertEqual(message.reply_text.await_args_list[-1].args[0], "**bold**")

    async def test_other_telegram_errors_propagate(self):
        message = SimpleNamespace(reply_text=AsyncMock(side_effect=BadRequest("Chat not found")))
        with self.assertRaises(BadRequest):
            await reply(message, "text", markdown=True)


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prompt = Path(self.temp.name) / "system.md"
        self.prompt.write_text("Original prompt")
        self.requests = []

    def client(self, handler, **changes):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        return ChatClient(config(self.prompt, **changes), http)

    async def test_no_tools_and_live_prompt_reload(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "test-model"}]})
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "<think>private</think>Answer"}}]})

        client = self.client(handler)
        await client.initialize()
        history = [{"role": "user", "content": "Hi"}]
        self.assertEqual(await client.complete(history), "Answer")
        self.prompt.write_text("Updated prompt")
        await client.complete([{"role": "user", "content": "Again"}])
        self.assertNotIn("tools", self.requests[0])
        self.assertNotIn("tool_choice", self.requests[0])
        self.assertEqual(self.requests[1]["messages"][0]["content"], "Updated prompt")
        self.assertEqual(history[-1], {"role": "assistant", "content": "Answer"})

    async def test_tool_round_trip_namespaces_collisions_and_uses_server_session(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "test-model"}]})
            payload = json.loads(request.content)
            if request.url.host == "mcp":
                server = request.url.path.split("/")[2]
                method = payload["method"]
                if method == "initialize":
                    return httpx.Response(200, json={"result": {}}, headers={"mcp-session-id": server})
                self.assertEqual(request.headers["mcp-session-id"], server)
                if method == "notifications/initialized":
                    self.assertNotIn("id", payload)
                    return httpx.Response(202)
                if method == "tools/list":
                    return httpx.Response(200, json={"result": {"tools": [{
                        "name": "health", "inputSchema": {"type": "object"},
                    }]}})
                self.assertEqual(payload["params"], {"name": "health", "arguments": {}})
                return httpx.Response(200, json={"result": {"content": [{"type": "text", "text": "OK"}]}})
            self.requests.append(payload)
            message = ({"tool_calls": [{"id": "call-1", "function": {
                "name": "time__health", "arguments": "{}",
            }}]} if len(self.requests) == 1 else {"content": "Healthy"})
            return httpx.Response(200, json={"choices": [{"message": message}]})

        client = self.client(handler, mcp_servers=("time", "markdown"))
        await client.initialize()
        history = [{"role": "user", "content": "Check health"}]
        self.assertEqual(await client.complete(history), "Healthy")
        self.assertEqual(set(client.mcp.routes), {"time__health", "markdown__health"})
        tool = self.requests[1]["messages"][-1]
        self.assertEqual(tool["tool_call_id"], "call-1")
        self.assertEqual(json.loads(tool["content"])["content"][0]["text"], "OK")
        self.assertEqual([item["role"] for item in history], ["user", "assistant", "tool", "assistant"])

    async def test_model_http_failure_is_not_an_empty_success(self):
        client = self.client(lambda request: httpx.Response(503))
        with self.assertRaises(httpx.HTTPStatusError):
            await client.complete([{"role": "user", "content": "Hi"}])

    async def test_invalid_tool_arguments_are_not_silently_replaced(self):
        client = self.client(lambda request: httpx.Response(200))
        client.mcp.routes["time__health"] = ("time", "health")
        with self.assertRaises(json.JSONDecodeError):
            await client.mcp.call("time__health", "invalid")

    async def test_vision_support_tracks_live_server_capabilities(self):
        responses = iter([{"modalities": {"vision": True}},
                          {"modalities": {"vision": False}}, {}])

        def handler(request):
            self.assertEqual(request.url.path, "/props")
            return httpx.Response(200, json=next(responses))

        client = self.client(handler)
        self.assertTrue(await client.supports_images())
        self.assertFalse(await client.supports_images())
        self.assertFalse(await client.supports_images())

    def test_history_keeps_whole_tool_turns(self):
        messages = [{"role": role} for role in (
            "user", "assistant", "user", "assistant", "tool", "assistant", "user", "assistant",
        )]
        self.assertEqual(trim_history(messages, 5), messages[6:])
        self.assertEqual(trim_history(messages[:6], 2), messages[2:6])


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def image_update(self):
        message = SimpleNamespace(reply_text=AsyncMock(), chat_id=10,
                                  media_group_id=None, caption="caption", text="/images")
        return SimpleNamespace(effective_user=SimpleNamespace(id=1),
                               effective_chat=SimpleNamespace(id=10), message=message)

    async def test_images_command_reports_live_model_support(self):
        chat = SimpleNamespace(supports_images=AsyncMock(return_value=False))
        bot = TelegramBot(config(Path("unused"), image_support_enabled=True), chat)
        update = self.image_update()
        await bot.command(update, SimpleNamespace())
        self.assertIn("disabled", update.message.reply_text.await_args.args[0])
        self.assertIn("model", update.message.reply_text.await_args.args[0])
        chat.supports_images.return_value = True
        await bot.command(update, SimpleNamespace())
        self.assertEqual(update.message.reply_text.await_args.args[0], "Image support: enabled.")

    async def test_nonvision_images_are_rejected_before_download_and_inference(self):
        chat = SimpleNamespace(supports_images=AsyncMock(return_value=False), complete=AsyncMock())
        bot = TelegramBot(config(Path("unused"), image_support_enabled=True), chat)
        bot.download_image = AsyncMock()
        update = self.image_update()
        await bot.image_message(update, SimpleNamespace())
        bot.download_image.assert_not_awaited()
        chat.complete.assert_not_awaited()
        self.assertIn("no vision support", update.message.reply_text.await_args.args[0])
        self.assertFalse(bot.chats)

    async def test_bot_setting_can_disable_images_for_a_vision_model(self):
        chat = SimpleNamespace(supports_images=AsyncMock(return_value=True))
        bot = TelegramBot(config(Path("unused")), chat)
        bot.download_image = AsyncMock()
        update = self.image_update()
        await bot.command(update, SimpleNamespace())
        self.assertIn("disabled in bot settings", update.message.reply_text.await_args.args[0])
        await bot.image_message(update, SimpleNamespace())
        bot.download_image.assert_not_awaited()
        chat.supports_images.assert_not_awaited()

    async def test_unreachable_server_does_not_allow_image_processing(self):
        chat = SimpleNamespace(supports_images=AsyncMock(side_effect=httpx.ConnectError("offline")))
        bot = TelegramBot(config(Path("unused"), image_support_enabled=True), chat)
        bot.download_image = AsyncMock()
        update = self.image_update()
        await bot.image_message(update, SimpleNamespace())
        bot.download_image.assert_not_awaited()
        self.assertIn("unavailable", update.message.reply_text.await_args.args[0])

    async def test_vision_model_accepts_images_when_bot_setting_allows_them(self):
        chat = SimpleNamespace(supports_images=AsyncMock(return_value=True))
        bot = TelegramBot(config(Path("unused"), image_support_enabled=True), chat)
        bot.download_image = AsyncMock(return_value="data:image/jpeg;base64,abc")
        bot.respond = AsyncMock()
        update = self.image_update()
        await bot.image_message(update, SimpleNamespace())
        bot.respond.assert_awaited_once_with(update.message, "caption", ["data:image/jpeg;base64,abc"])

    async def test_unauthorized_images_are_not_downloaded(self):
        bot = TelegramBot(config(Path("unused"), image_support_enabled=True), SimpleNamespace())
        message = SimpleNamespace(reply_text=AsyncMock(), chat_id=10)
        update = SimpleNamespace(effective_user=SimpleNamespace(id=2),
                                 effective_chat=SimpleNamespace(id=10), message=message)
        bot.download_image = AsyncMock()
        await bot.image_message(update, SimpleNamespace())
        bot.download_image.assert_not_awaited()
        self.assertIn("Access denied", message.reply_text.await_args.args[0])

    async def test_concurrent_messages_in_one_chat_keep_order(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def complete(history):
            if history[-1]["content"] == "first":
                started.set()
                await release.wait()
            history.append({"role": "assistant", "content": "answer"})
            return "answer"

        bot = TelegramBot(config(Path("unused")), SimpleNamespace(complete=complete))
        message = SimpleNamespace(reply_text=AsyncMock(), chat_id=10)
        first = asyncio.create_task(bot.respond(message, "first", []))
        await asyncio.wait_for(started.wait(), timeout=1)
        second = asyncio.create_task(bot.respond(message, "second", []))
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
        self.assertEqual([item["content"] for item in bot.chats[10].history],
                         ["first", "answer", "second", "answer"])

    async def test_images_reach_the_model_as_data_urls(self):
        async def complete(history):
            self.assertEqual(history[-1]["content"], [
                {"type": "text", "text": "caption"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
            ])
            history.append({"role": "assistant", "content": "Seen"})
            return "Seen"

        bot = TelegramBot(config(Path("unused")), SimpleNamespace(complete=complete))
        await bot.respond(SimpleNamespace(reply_text=AsyncMock(), chat_id=10),
                          "caption", ["data:image/jpeg;base64,abc"])


if __name__ == "__main__":
    unittest.main()
