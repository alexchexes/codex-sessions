from datetime import datetime
from math import isfinite
from typing import Any

from codex_sessions.sessions.display import local_timezone_offset_label

DEFAULT_GAP_THRESHOLD_SECONDS = 4 * 60 * 60
DEFAULT_TOOL_DURATION_THRESHOLD_SECONDS = 30.0


def format_markdown_timestamp(value: datetime) -> str:
    local_value = value.astimezone()
    return f"{local_value.strftime('%Y-%m-%d %H:%M:%S')} ({local_timezone_offset_label(value)})"


def format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 1:
        return f"{seconds:.2f}".rstrip("0").rstrip(".") + "s"
    if seconds < 60:
        if seconds < 10 and not seconds.is_integer():
            return f"{seconds:.1f}s"
        return f"{round(seconds):.0f}s"

    total_seconds = round(seconds)
    minutes, remainder_seconds = divmod(total_seconds, 60)
    if minutes < 60:
        if remainder_seconds:
            return f"{minutes}m {remainder_seconds}s"
        return f"{minutes}m"

    hours, remainder_minutes = divmod(minutes, 60)
    if hours < 24:
        if remainder_minutes:
            return f"{hours}h {remainder_minutes}m"
        return f"{hours}h"

    days, remainder_hours = divmod(hours, 24)
    if remainder_hours:
        return f"{days}d {remainder_hours}h"
    return f"{days}d"


def numeric_duration_part(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if parsed >= 0 and isfinite(parsed) else None


def duration_value_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return numeric_duration_part(value)
    if not isinstance(value, dict):
        return None

    seconds = numeric_duration_part(value.get("secs"))
    if seconds is None:
        seconds = numeric_duration_part(value.get("seconds"))
    nanos = numeric_duration_part(value.get("nanos"))
    if nanos is None:
        nanos = numeric_duration_part(value.get("nanoseconds"))
    if seconds is None and nanos is None:
        millis = numeric_duration_part(value.get("millis"))
        if millis is None:
            millis = numeric_duration_part(value.get("milliseconds"))
        return millis / 1000 if millis is not None else None

    return (seconds or 0.0) + (nanos or 0.0) / 1_000_000_000


def event_duration_seconds(payload: dict[str, Any]) -> float | None:
    duration = duration_value_seconds(payload.get("duration"))
    if duration is not None:
        return duration
    duration_ms = numeric_duration_part(payload.get("duration_ms"))
    return duration_ms / 1000 if duration_ms is not None else None
