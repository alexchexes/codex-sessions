import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_sessions.codex.state import StateCacheBackup, temp_path_for
from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.sessions.index import normalize_session_id

MAX_EXPORT_TITLE_SLUG_CHARS = 80
ROLLOUT_FILENAME_DATE_RE = re.compile(r"^rollout-(\d{4})-(\d{2})-(\d{2})T")


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    sha256: str


@dataclass(frozen=True)
class ImportSessionPlan:
    source_path: Path
    target_path: Path
    session_index_path: Path
    session_id: str
    thread_name: str
    started_at: datetime | None
    ended_at: datetime | None
    index_action: str
    existing_index_thread_name: str | None
    source_fingerprint: FileFingerprint
    rollout_will_be_rewritten: bool


@dataclass(frozen=True)
class ImportSessionResult:
    plan: ImportSessionPlan
    session_index_backup_path: Path | None
    state_cache_backups: tuple[StateCacheBackup, ...]


@dataclass(frozen=True)
class ExportSessionPlan:
    source_path: Path
    output_path: Path
    session_id: str
    thread_name: str
    started_at: datetime | None
    ended_at: datetime | None
    rollout_will_be_rewritten: bool
    overwrite: bool


@dataclass(frozen=True)
class ExportSessionResult:
    plan: ExportSessionPlan


def file_fingerprint(path: Path) -> FileFingerprint:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(1024 * 1024):
            digest.update(chunk)
    return FileFingerprint(size=path.stat().st_size, sha256=digest.hexdigest())


def short_sha256(fingerprint: FileFingerprint | None) -> str:
    return fingerprint.sha256[:12] if fingerprint is not None else "UNKNOWN"


def format_fingerprint(fingerprint: FileFingerprint) -> str:
    return f"{fingerprint.size} bytes, sha256 {short_sha256(fingerprint)}"


def rollout_filename_date(path: Path) -> tuple[str, str, str] | None:
    match = ROLLOUT_FILENAME_DATE_RE.match(path.name)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def export_title_slug(thread_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", thread_name).strip("-")
    if not slug:
        return "session"
    return slug[:MAX_EXPORT_TITLE_SLUG_CHARS].rstrip("-") or "session"


def output_arg_looks_like_directory(raw_output: Path) -> bool:
    raw_output_text = str(raw_output)
    return raw_output_text.endswith(("/", "\\")) or raw_output.suffix == ""


def resolve_export_output_path(raw_output: Path | None, default_filename: str) -> Path:
    if raw_output is None:
        return Path.cwd() / default_filename
    output_path = raw_output.expanduser()
    if output_path.exists() and output_path.is_dir():
        return output_path / default_filename
    if not output_path.exists() and output_arg_looks_like_directory(raw_output):
        return output_path / default_filename
    return output_path


def thread_name_updated_session_id(payload: Mapping[str, Any]) -> str | None:
    if payload.get("type") != "thread_name_updated":
        return None
    thread_id = payload.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def thread_name_updated_name(payload: Mapping[str, Any]) -> str | None:
    if payload.get("type") != "thread_name_updated":
        return None
    thread_name = payload.get("thread_name")
    if not isinstance(thread_name, str):
        return None
    normalized = thread_name.strip()
    return normalized or None


def thread_name_updated_matches_session(payload: Mapping[str, Any], session_id: str | None) -> bool:
    event_session_id = thread_name_updated_session_id(payload)
    if event_session_id is None:
        return False
    if session_id is None:
        return True
    return normalize_session_id(event_session_id) == normalize_session_id(session_id)


def read_rollout_records(path: Path) -> list[dict[str, Any]]:
    return [record for _, record in iter_jsonl_objects(path)]


def write_rollout_records(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    serialized = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records
    )
    temp_path = temp_path_for(path)
    temp_path.write_text(serialized, encoding="utf-8")
    temp_path.replace(path)


def thread_name_update_event(
    session_id: str, thread_name: str, timestamp: str | None
) -> dict[str, Any]:
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": "event_msg",
        "payload": {
            "type": "thread_name_updated",
            "thread_id": session_id,
            "thread_name": thread_name,
        },
    }


def renamed_rollout_records(
    records: Sequence[dict[str, Any]], session_id: str, new_thread_name: str
) -> tuple[list[dict[str, Any]], str | None, bool]:
    latest_index: int | None = None
    latest_thread_name: str | None = None
    for index, record in enumerate(records):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if not thread_name_updated_matches_session(payload, session_id):
            continue
        latest_index = index
        latest_thread_name = thread_name_updated_name(payload) or latest_thread_name

    if latest_index is not None:
        if latest_thread_name == new_thread_name:
            return list(records), latest_thread_name, False
        updated_records = list(records)
        updated_record = dict(updated_records[latest_index])
        updated_payload = dict(updated_record.get("payload", {}))
        updated_payload["thread_id"] = session_id
        updated_payload["thread_name"] = new_thread_name
        updated_record["payload"] = updated_payload
        updated_records[latest_index] = updated_record
        return updated_records, latest_thread_name, True

    first_timestamp = None
    if records:
        raw_timestamp = records[0].get("timestamp")
        if isinstance(raw_timestamp, str):
            first_timestamp = raw_timestamp
    inserted_record = thread_name_update_event(session_id, new_thread_name, first_timestamp)
    insert_at = 1 if records else 0
    updated_records = list(records)
    updated_records.insert(insert_at, inserted_record)
    return updated_records, None, True
