#!/usr/bin/env python3
"""Local MCP server for time and timezone utilities."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("time")
LOCAL_TIMEZONE = "UTC"


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    tz_name = (timezone_name or LOCAL_TIMEZONE).strip()
    if not tz_name:
        raise ValueError("timezone must not be empty")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone '{tz_name}'") from exc


def _parse_time_input(raw_time: str, source_tz: ZoneInfo) -> datetime:
    value = raw_time.strip()
    if not value:
        raise ValueError("time must not be empty")

    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None

    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=source_tz)
        return parsed

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_time = datetime.strptime(value, fmt).time()
            now_local = datetime.now(source_tz)
            return datetime(
                now_local.year,
                now_local.month,
                now_local.day,
                parsed_time.hour,
                parsed_time.minute,
                parsed_time.second,
                tzinfo=source_tz,
            )
        except ValueError:
            continue

    raise ValueError(
        "invalid time format. Use ISO-8601 datetime (e.g. 2026-03-27T14:30:00) "
        "or HH:MM / HH:MM:SS"
    )


@mcp.tool()
def health() -> dict[str, str]:
    """Health check and active default local timezone."""
    return {
        "status": "ok",
        "local_timezone": LOCAL_TIMEZONE,
    }


@mcp.tool()
def get_current_time(timezone: str | None = None) -> dict[str, Any]:
    """Get current date/time in an IANA timezone."""
    tz = _resolve_timezone(timezone)
    now = datetime.now(tz)
    utc_now = now.astimezone(UTC)
    return {
        "timezone": str(tz.key),
        "iso_datetime": now.isoformat(),
        "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_iso_datetime": utc_now.isoformat(),
    }


@mcp.tool()
def convert_time(
    time: str,
    target_timezone: str,
    source_timezone: str | None = None,
) -> dict[str, str]:
    """Convert an input time from source timezone to target timezone."""
    source_tz = _resolve_timezone(source_timezone)
    target_tz = _resolve_timezone(target_timezone)

    source_dt = _parse_time_input(time, source_tz)
    target_dt = source_dt.astimezone(target_tz)

    return {
        "source_timezone": source_tz.key,
        "target_timezone": target_tz.key,
        "source_time": source_dt.isoformat(),
        "target_time": target_dt.isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run local MCP time server")
    parser.add_argument(
        "--local-timezone",
        default=os.environ.get("LOCAL_TIMEZONE", "UTC"),
        help="Default local timezone in IANA format, e.g. Europe/Amsterdam",
    )
    args = parser.parse_args()

    local_tz = _resolve_timezone(args.local_timezone)
    LOCAL_TIMEZONE = local_tz.key

    mcp.run()
