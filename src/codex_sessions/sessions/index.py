import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from codex_sessions.codex.state import temp_path_for
from codex_sessions.core.json_streams import iter_concatenated_json_objects
from codex_sessions.core.timestamps import parse_timestamp

SESSION_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class SessionIndexError(ValueError):
    pass


class SessionIndexCandidate(Protocol):
    @property
    def session_id(self) -> str: ...

    @property
    def thread_name(self) -> str: ...

    @property
    def updated_at(self) -> datetime | None: ...


@dataclass(frozen=True)
class SessionIndexEntry:
    session_id: str
    thread_name: str
    updated_at: datetime | None


def normalize_session_id(session_id: str) -> str:
    return session_id.lower()


def is_session_id(value: str) -> bool:
    return SESSION_ID_RE.fullmatch(value) is not None


def read_session_index(index_path: Path) -> list[SessionIndexEntry]:
    if not index_path.exists():
        return []

    entries = []
    # Some observed indexes have concatenated JSON objects instead of clean JSONL lines.
    for _, record in iter_concatenated_json_objects(index_path):
        if not isinstance(record, dict):
            continue
        session_id = record.get("id")
        if not isinstance(session_id, str) or not session_id:
            continue
        thread_name = record.get("thread_name")
        entries.append(
            SessionIndexEntry(
                session_id=session_id,
                thread_name=thread_name if isinstance(thread_name, str) else "",
                updated_at=parse_timestamp(record.get("updated_at")),
            )
        )
    return entries


def format_session_index_timestamp(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    converted = timestamp.astimezone(timezone.utc)
    return converted.isoformat().replace("+00:00", "Z")


def session_index_record_for_candidate(candidate: SessionIndexCandidate) -> dict[str, str]:
    return {
        "id": candidate.session_id,
        "thread_name": candidate.thread_name,
        "updated_at": format_session_index_timestamp(candidate.updated_at),
    }


def append_session_index_records(
    index_path: Path, candidates: Sequence[SessionIndexCandidate]
) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    existing_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    # Preserve existing index bytes as much as possible; append only the repaired entries.
    separator = "\n" if existing_text and not existing_text.endswith("\n") else ""
    appended_text = "".join(
        json.dumps(
            session_index_record_for_candidate(candidate),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
        for candidate in candidates
    )
    temp_path = temp_path_for(index_path)
    temp_path.write_text(f"{existing_text}{separator}{appended_text}", encoding="utf-8")
    temp_path.replace(index_path)


def session_index_records(index_path: Path) -> list[Any]:
    if not index_path.exists():
        raise SessionIndexError(f"session_index.jsonl not found: {index_path}")
    return [record for _, record in iter_concatenated_json_objects(index_path)]


def write_session_index_records(index_path: Path, records: Sequence[Any]) -> None:
    serialized = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records
    )
    temp_path = temp_path_for(index_path)
    temp_path.write_text(serialized, encoding="utf-8")
    temp_path.replace(index_path)


def session_index_record_id(record: Mapping[str, Any]) -> str | None:
    session_id = record.get("id")
    return session_id if isinstance(session_id, str) and session_id else None


def session_index_record_thread_name(record: Mapping[str, Any]) -> str:
    thread_name = record.get("thread_name")
    return thread_name if isinstance(thread_name, str) else ""


def matching_session_index_records(
    records: Sequence[Any], target: str
) -> tuple[tuple[int, dict[str, Any]], ...]:
    target_is_id = is_session_id(target)
    matches: list[tuple[int, dict[str, Any]]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        session_id = session_index_record_id(record)
        if session_id is None:
            continue
        if target_is_id:
            if normalize_session_id(session_id) == normalize_session_id(target):
                matches.append((index, record))
        elif session_index_record_thread_name(record) == target:
            matches.append((index, record))
    return tuple(matches)


def resolve_session_index_record(records: Sequence[Any], target: str) -> tuple[int, dict[str, Any]]:
    matches = matching_session_index_records(records, target)
    if len(matches) == 1:
        return matches[0]

    if not matches:
        if is_session_id(target):
            raise SessionIndexError(f"No session_index.jsonl entry found for ID: {target}")
        raise SessionIndexError(f"No session_index.jsonl entry found for title: {target}")

    rendered_matches = ", ".join(
        session_index_record_id(record) or "<missing id>" for _, record in matches
    )
    if is_session_id(target):
        raise SessionIndexError(
            f"Multiple session_index.jsonl entries found for ID {target}: {rendered_matches}"
        )
    raise SessionIndexError(
        f"Multiple session_index.jsonl entries matched title {target!r}: "
        f"{rendered_matches}. Re-run with one ID."
    )
