#!/usr/bin/env python3
"""Local MCP server for scoped Markdown file operations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("markdown")

DEFAULT_TARGET = Path.cwd()
SCOPED_ROOT = DEFAULT_TARGET
SCOPED_FILE: Path | None = None


def _looks_like_markdown_file(path: Path) -> bool:
    return path.suffix.lower() == ".md"


def _resolve_within_scope(path: Path) -> Path:
    resolved = path.resolve()
    if SCOPED_FILE is not None:
        if resolved != SCOPED_FILE:
            raise ValueError(f"path is outside allowed file scope: {resolved}")
        return resolved

    try:
        resolved.relative_to(SCOPED_ROOT)
    except ValueError as exc:
        raise ValueError(f"path is outside allowed root scope: {resolved}") from exc
    return resolved


def _coerce_target_path(path: str | None) -> Path:
    if SCOPED_FILE is not None:
        if not path:
            return SCOPED_FILE
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = SCOPED_ROOT / candidate
        return _resolve_within_scope(candidate)

    if not path or not path.strip():
        raise ValueError("path is required")

    candidate = Path(path.strip())
    if not candidate.is_absolute():
        candidate = SCOPED_ROOT / candidate
    return _resolve_within_scope(candidate)


def _assert_markdown_path(path: Path) -> None:
    if path.suffix.lower() != ".md":
        raise ValueError("only .md files are allowed")


def _relative_to_scope(path: Path) -> str:
    if SCOPED_FILE is not None:
        return path.name
    return str(path.relative_to(SCOPED_ROOT))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@mcp.tool()
def health() -> dict[str, Any]:
    """Health and scope information for this markdown server."""
    return {
        "status": "ok",
        "scope_mode": "single-file" if SCOPED_FILE is not None else "directory",
        "root": str(SCOPED_ROOT),
        "single_file": str(SCOPED_FILE) if SCOPED_FILE is not None else None,
    }


@mcp.tool()
def list_markdown_files(subdir: str = "", recursive: bool = True) -> dict[str, Any]:
    """List markdown files in the allowed scope."""
    if SCOPED_FILE is not None:
        return {
            "root": str(SCOPED_ROOT),
            "files": [SCOPED_FILE.name],
            "count": 1,
        }

    base = SCOPED_ROOT if not subdir.strip() else _coerce_target_path(subdir)
    if not base.exists():
        raise ValueError(f"path does not exist: {base}")
    if not base.is_dir():
        raise ValueError(f"path is not a directory: {base}")

    pattern = "**/*.md" if recursive else "*.md"
    files = [
        str(item.relative_to(SCOPED_ROOT))
        for item in sorted(base.glob(pattern))
        if item.is_file() and item.suffix.lower() == ".md"
    ]
    return {
        "root": str(SCOPED_ROOT),
        "files": files,
        "count": len(files),
    }


@mcp.tool()
def read_markdown(path: str | None = None) -> dict[str, Any]:
    """Read a markdown file in the allowed scope."""
    target = _coerce_target_path(path)
    _assert_markdown_path(target)
    if not target.exists():
        raise ValueError(f"file not found: {target}")
    if not target.is_file():
        raise ValueError(f"path is not a file: {target}")

    content = target.read_text(encoding="utf-8")
    return {
        "path": _relative_to_scope(target),
        "chars": len(content),
        "content": content,
    }


@mcp.tool()
def write_markdown(
    path: str | None, content: str, overwrite: bool = True
) -> dict[str, Any]:
    """Write markdown content (atomic write)."""
    target = _coerce_target_path(path)
    _assert_markdown_path(target)

    existed_before = target.exists()
    if existed_before and not overwrite:
        raise ValueError(f"file already exists and overwrite=false: {target}")

    _atomic_write(target, content)
    return {
        "path": _relative_to_scope(target),
        "chars": len(content),
        "overwrote": existed_before,
    }


@mcp.tool()
def append_markdown(
    path: str | None, content: str, ensure_leading_newline: bool = True
) -> dict[str, Any]:
    """Append content to a markdown file."""
    target = _coerce_target_path(path)
    _assert_markdown_path(target)

    existing = ""
    if target.exists():
        if not target.is_file():
            raise ValueError(f"path is not a file: {target}")
        existing = target.read_text(encoding="utf-8")

    prefix = (
        "\n"
        if ensure_leading_newline and existing and not existing.endswith("\n")
        else ""
    )
    updated = f"{existing}{prefix}{content}"
    _atomic_write(target, updated)
    return {
        "path": _relative_to_scope(target),
        "chars": len(updated),
        "appended_chars": len(content),
    }


@mcp.tool()
def replace_in_markdown(
    path: str | None, old_text: str, new_text: str, max_replacements: int = -1
) -> dict[str, Any]:
    """Replace text in a markdown file."""
    target = _coerce_target_path(path)
    _assert_markdown_path(target)
    if not target.exists() or not target.is_file():
        raise ValueError(f"file not found: {target}")
    if old_text == "":
        raise ValueError("old_text must not be empty")

    original = target.read_text(encoding="utf-8")
    total_matches = original.count(old_text)
    updated = original.replace(
        old_text, new_text, max_replacements if max_replacements >= 0 else -1
    )

    replacements = (
        total_matches if max_replacements < 0 else min(total_matches, max_replacements)
    )
    _atomic_write(target, updated)
    return {
        "path": _relative_to_scope(target),
        "replacements": replacements,
        "chars": len(updated),
    }


@mcp.tool()
def delete_markdown(path: str | None, missing_ok: bool = False) -> dict[str, Any]:
    """Delete a markdown file in scope."""
    target = _coerce_target_path(path)
    _assert_markdown_path(target)

    if not target.exists():
        if missing_ok:
            return {
                "path": _relative_to_scope(target),
                "deleted": False,
            }
        raise ValueError(f"file not found: {target}")

    if not target.is_file():
        raise ValueError(f"path is not a file: {target}")

    target.unlink()
    return {
        "path": _relative_to_scope(target),
        "deleted": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run local MCP markdown server")
    parser.add_argument(
        "--target",
        default=os.environ.get("MARKDOWN_TARGET", str(Path.cwd())),
        help="Allowed markdown target: a directory or a single .md file",
    )
    args = parser.parse_args()

    target_path = Path(args.target).expanduser()
    if not target_path.is_absolute():
        target_path = Path.cwd() / target_path
    target_path = target_path.resolve()

    if _looks_like_markdown_file(target_path):
        SCOPED_FILE = target_path
        SCOPED_ROOT = target_path.parent
    else:
        SCOPED_ROOT = target_path
        SCOPED_FILE = None

    if not SCOPED_ROOT.exists():
        raise RuntimeError(f"root directory does not exist: {SCOPED_ROOT}")
    if not SCOPED_ROOT.is_dir():
        raise RuntimeError(f"root path is not a directory: {SCOPED_ROOT}")

    if SCOPED_FILE is not None and not SCOPED_FILE.exists():
        raise RuntimeError(f"markdown file does not exist: {SCOPED_FILE}")
    if SCOPED_FILE is not None and not SCOPED_FILE.is_file():
        raise RuntimeError(f"target path is not a file: {SCOPED_FILE}")

    mcp.run()
