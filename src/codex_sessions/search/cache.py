import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from codex_sessions.sessions.cache import (
    cached_session_metadata,
    session_cache_entry_from_document,
    session_cache_key,
)
from codex_sessions.sessions.documents import SearchDocument

SEARCH_CACHE_VERSION = 4
SEARCH_CACHE_RELATIVE_PATH = Path("cache") / "codex-sessions" / "search-v4.json"


def search_cache_path(codex_home: Path) -> Path:
    return codex_home / SEARCH_CACHE_RELATIVE_PATH


def search_cache_key(path: Path) -> str:
    return session_cache_key(path)


def read_search_cache(cache_path: Path) -> dict[str, Any]:
    try:
        raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw_cache, dict):
        return {}
    if raw_cache.get("version") != SEARCH_CACHE_VERSION:
        return {}
    entries = raw_cache.get("entries")
    if not isinstance(entries, dict):
        return {}
    return entries


def write_search_cache(cache_path: Path, entries: Mapping[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
    cache_data = {
        "version": SEARCH_CACHE_VERSION,
        "entries": entries,
    }
    temp_path.write_text(
        json.dumps(cache_data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(cache_path)


def cached_search_document(
    entry: Any, path: Path, stat_result: os.stat_result, redaction: str
) -> SearchDocument | None:
    if not isinstance(entry, dict):
        return None
    metadata = cached_session_metadata(entry, path, stat_result)
    if metadata is None:
        return None
    if entry.get("redaction") != redaction:
        return None

    visible_lines = string_tuple(entry.get("visible_lines"))
    metadata_lines = string_tuple(entry.get("metadata_lines"))
    tool_input_lines = string_tuple(entry.get("tool_input_lines"))
    tool_output_lines = string_tuple(entry.get("tool_output_lines"))
    if (
        visible_lines is None
        or metadata_lines is None
        or tool_input_lines is None
        or tool_output_lines is None
    ):
        return None

    return SearchDocument(
        session_id=metadata.session_id,
        thread_name=metadata.thread_name,
        started_at=metadata.started_at,
        ended_at=metadata.ended_at,
        visible_lines=visible_lines,
        metadata_lines=metadata_lines,
        tool_input_lines=tool_input_lines,
        tool_output_lines=tool_output_lines,
    )


def string_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def search_cache_entry(
    path: Path, stat_result: os.stat_result, document: SearchDocument, redaction: str
) -> dict[str, Any]:
    return {
        **session_cache_entry_from_document(path, stat_result, document),
        "redaction": redaction,
        "visible_lines": list(document.visible_lines),
        "metadata_lines": list(document.metadata_lines),
        "tool_input_lines": list(document.tool_input_lines),
        "tool_output_lines": list(document.tool_output_lines),
    }


def prune_missing_search_cache_entries(entries: dict[str, Any]) -> bool:
    removed_any = False
    for key, entry in list(entries.items()):
        path_text = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path_text, str):
            del entries[key]
            removed_any = True
            continue
        if not Path(path_text).exists():
            del entries[key]
            removed_any = True
    return removed_any
