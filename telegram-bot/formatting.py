"""A small Markdown subset for Telegram: headings, emphasis, and code."""

from __future__ import annotations

import html
import re

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

# Keep each chunk below Telegram's text limit, including non-BMP characters.
CHUNK_SIZE = 1800
INLINE = re.compile(r"(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(?<!\w)(\*[^*\n]+\*|_[^_\n]+_)(?!\w)")
THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL)


def strip_reasoning(text: str) -> str:
    return THINK_BLOCK.sub("", text).strip()


def _inline(text: str) -> str:
    parts = []
    end = 0
    for match in INLINE.finditer(text):
        parts.append(html.escape(text[end : match.start()]))
        token = match.group()
        if match.group(1):
            tag, content = "code", token[1:-1]
        elif match.group(2):
            tag, content = "b", token[2:-2]
        else:
            tag, content = "i", token[1:-1]
        parts.append(f"<{tag}>{html.escape(content)}</{tag}>")
        end = match.end()
    parts.append(html.escape(text[end:]))
    return "".join(parts)


def telegram_html(text: str) -> str:
    parts = []
    # Code fences are handled before inline formatting, including unfinished fences.
    for index, part in enumerate(re.split(r"^```[^\n]*\n?|^```\s*$", text, flags=re.MULTILINE)):
        if index % 2:
            parts.append(f"<pre><code>{html.escape(part)}</code></pre>")
            continue
        lines = []
        for line in part.split("\n"):
            heading = re.match(r"^#{1,6}\s+(.+)", line)
            lines.append(f"<b>{html.escape(heading[1])}</b>" if heading else _inline(line))
        parts.append("\n".join(lines))
    return "".join(parts)


async def reply(message: Message, text: str, *, markdown: bool = False) -> None:
    """Commands are plain text; model replies use the supported Markdown subset."""
    text = (strip_reasoning(text) if markdown else text) or "(No content returned)"
    # Long replies use plain text so chunks cannot split HTML tags or entities.
    if not markdown or len(text) > CHUNK_SIZE:
        for start in range(0, len(text), CHUNK_SIZE):
            await message.reply_text(text[start : start + CHUNK_SIZE], parse_mode=None)
        return
    try:
        await message.reply_text(telegram_html(text), parse_mode=ParseMode.HTML)
    except BadRequest as exc:
        if "parse" not in str(exc).lower():
            raise
        await message.reply_text(text, parse_mode=None)
