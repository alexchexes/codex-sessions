import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.sessions.documents import SearchDocument
from codex_sessions.sessions.rollout import FileFingerprint, file_fingerprint

SESSION_CACHE_VERSION = 1
SESSION_CACHE_RELATIVE_PATH = Path("cache") / "codex-sessions" / "sessions-v1.json"


@dataclass(frozen=True)
class SessionCacheEntry:
    path: Path
    size: int
    mtime_ns: int
    sha256: str | None
    session_id: str | None
    thread_name: str | None
    started_at: datetime | None
    ended_at: datetime | None


def session_cache_path(codex_home: Path) -> Path:
    return codex_home / SESSION_CACHE_RELATIVE_PATH


def session_cache_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def read_session_cache(cache_path: Path) -> dict[str, Any]:
    try:
        raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(raw_cache, dict):
        return {}
    if raw_cache.get("version") != SESSION_CACHE_VERSION:
        return {}
    entries = raw_cache.get("entries")
    if not isinstance(entries, dict):
        return {}
    return entries


def write_session_cache(cache_path: Path, entries: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
    cache_data = {
        "version": SESSION_CACHE_VERSION,
        "entries": entries,
    }
    temp_path.write_text(
        json.dumps(cache_data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(cache_path)


def cached_session_metadata(
    entry: Any, path: Path, stat_result: os.stat_result
) -> SessionCacheEntry | None:
    """Return cached metadata only when the file identity still matches current stat data."""
    if not isinstance(entry, dict):
        return None
    if entry.get("path") != str(path.resolve()):
        return None
    size = entry.get("size")
    mtime_ns = entry.get("mtime_ns")
    if not isinstance(size, int) or not isinstance(mtime_ns, int):
        return None
    if size != stat_result.st_size or mtime_ns != stat_result.st_mtime_ns:
        return None

    sha256 = entry.get("sha256")
    if sha256 is not None and not valid_sha256(sha256):
        return None

    session_id = entry.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        return None
    thread_name = entry.get("thread_name")
    if thread_name is not None and not isinstance(thread_name, str):
        return None

    return SessionCacheEntry(
        path=path.resolve(),
        size=size,
        mtime_ns=mtime_ns,
        sha256=sha256,
        session_id=session_id,
        thread_name=thread_name,
        started_at=parse_timestamp(entry.get("started_at")),
        ended_at=parse_timestamp(entry.get("ended_at")),
    )


def valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def session_cache_entry(
    path: Path,
    stat_result: os.stat_result,
    *,
    session_id: str | None = None,
    thread_name: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    fingerprint: FileFingerprint | None = None,
) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "sha256": fingerprint.sha256 if fingerprint is not None else None,
        "session_id": session_id,
        "thread_name": thread_name,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": ended_at.isoformat() if ended_at else None,
    }


def session_cache_entry_from_document(
    path: Path,
    stat_result: os.stat_result,
    document: SearchDocument,
    *,
    fingerprint: FileFingerprint | None = None,
) -> dict[str, Any]:
    return session_cache_entry(
        path,
        stat_result,
        session_id=document.session_id,
        thread_name=document.thread_name,
        started_at=document.started_at,
        ended_at=document.ended_at,
        fingerprint=fingerprint,
    )


def cached_file_fingerprint(
    entry: Any, path: Path, stat_result: os.stat_result
) -> FileFingerprint | None:
    metadata = cached_session_metadata(entry, path, stat_result)
    if metadata is None or metadata.sha256 is None:
        return None
    return FileFingerprint(size=metadata.size, sha256=metadata.sha256)


def file_fingerprint_from_session_cache(
    path: Path,
    entries: dict[str, Any] | None,
    *,
    rebuild_cache: bool = False,
) -> tuple[FileFingerprint, os.stat_result, bool]:
    """Reuse a cached SHA only when path, size, and mtime still match exactly."""
    stat_result = path.stat()
    cache_key = session_cache_key(path)
    metadata = (
        cached_session_metadata(entries.get(cache_key), path, stat_result)
        if entries is not None and not rebuild_cache
        else None
    )
    if metadata is not None and metadata.sha256 is not None:
        return FileFingerprint(size=metadata.size, sha256=metadata.sha256), stat_result, False

    fingerprint = file_fingerprint(path)
    updated_stat_result = path.stat()
    if entries is not None:
        entries[cache_key] = session_cache_entry(
            path,
            updated_stat_result,
            session_id=metadata.session_id if metadata is not None else None,
            thread_name=metadata.thread_name if metadata is not None else None,
            started_at=metadata.started_at if metadata is not None else None,
            ended_at=metadata.ended_at if metadata is not None else None,
            fingerprint=fingerprint,
        )
    return fingerprint, updated_stat_result, entries is not None


def prune_missing_session_cache_entries(entries: dict[str, Any]) -> bool:
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
