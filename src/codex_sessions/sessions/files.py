from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.sessions.index import SESSION_ID_RE, is_session_id, normalize_session_id

FILENAME_ID_MISMATCH_STATUS = "FILENAME ID MISMATCH"
INVALID_SESSION_META_FILENAME_ID_STATUS = "INVALID RECORD-1 session_meta; USING ID FROM FILENAME"
INVALID_SESSION_META_NO_ID_STATUS = "INVALID RECORD-1 session_meta; NO SESSION ID"


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str | None
    is_canonical: bool
    warning: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class SessionFileMetadata:
    identity: SessionIdentity
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class SessionFile:
    path: Path
    relative_path: str
    session_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    session_id_is_canonical: bool = False
    identity_warning: str | None = None
    identity_status: str | None = None
    modified_at: datetime | None = None


def session_id_from_path(path: Path) -> str | None:
    stem = path.stem
    for match in SESSION_ID_RE.finditer(stem):
        if match.end() == len(stem):
            return match.group(0)
    return None


def canonical_session_id_from_record(record: dict[str, Any] | None) -> str | None:
    if record is None or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("id")
    if not isinstance(session_id, str) or not is_session_id(session_id):
        return None
    return session_id


def resolve_session_identity(path: Path, first_record: dict[str, Any] | None) -> SessionIdentity:
    metadata_id = canonical_session_id_from_record(first_record)
    filename_id = session_id_from_path(path)
    if metadata_id is not None:
        if filename_id is not None and normalize_session_id(filename_id) != normalize_session_id(
            metadata_id
        ):
            return SessionIdentity(
                session_id=metadata_id,
                is_canonical=True,
                warning=(
                    f"trailing filename session ID {filename_id} does not match record-1 "
                    f"session_meta.payload.id {metadata_id}; using metadata ID"
                ),
                status=FILENAME_ID_MISMATCH_STATUS,
            )
        return SessionIdentity(session_id=metadata_id, is_canonical=True)

    invalid_metadata = "record 1 must be session_meta with a valid UUID payload.id"
    if filename_id is not None:
        return SessionIdentity(
            session_id=filename_id,
            is_canonical=False,
            warning=f"{invalid_metadata}; using trailing filename session ID {filename_id}",
            status=INVALID_SESSION_META_FILENAME_ID_STATUS,
        )
    return SessionIdentity(
        session_id=None,
        is_canonical=False,
        warning=f"{invalid_metadata}; no trailing filename session ID is available",
        status=INVALID_SESSION_META_NO_ID_STATUS,
    )


def read_session_identity(path: Path) -> SessionIdentity:
    first_record: dict[str, Any] | None = None
    try:
        first_record = next(iter(iter_jsonl_objects(path)))[1]
    except StopIteration:
        pass
    except (OSError, ValueError):
        pass
    return resolve_session_identity(path, first_record)


def session_id_from_metadata(path: Path) -> str | None:
    try:
        first_record = next(iter(iter_jsonl_objects(path)))[1]
    except (StopIteration, OSError, ValueError):
        return None
    return canonical_session_id_from_record(first_record)


def read_session_file_metadata(
    path: Path, *, include_ended_at: bool = False
) -> SessionFileMetadata:
    """Read rollout identity and timestamps; full last-timestamp scans are opt-in."""
    first_record: dict[str, Any] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    try:
        for count, (_, record) in enumerate(iter_jsonl_objects(path), start=1):
            if first_record is None:
                first_record = record
            record_timestamp = parse_timestamp(record.get("timestamp"))
            if include_ended_at and record_timestamp is not None:
                ended_at = record_timestamp

            payload = record.get("payload")
            if started_at is None:
                started_at = record_timestamp
                if started_at is None and isinstance(payload, dict):
                    started_at = parse_timestamp(payload.get("timestamp"))
            if not include_ended_at and started_at is not None:
                break
            if count >= 20:
                if include_ended_at:
                    # Last-interaction time needs the whole file; start usually does not.
                    continue
                break
    except (OSError, ValueError):
        pass
    return SessionFileMetadata(
        identity=resolve_session_identity(path, first_record),
        started_at=started_at,
        ended_at=ended_at,
    )


def session_file_metadata(
    path: Path, *, include_ended_at: bool = False
) -> tuple[str | None, datetime | None, datetime | None]:
    metadata = read_session_file_metadata(path, include_ended_at=include_ended_at)
    return metadata.identity.session_id, metadata.started_at, metadata.ended_at


def format_session_file_path(path: Path, sessions_dir: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(sessions_dir.resolve())
    except ValueError:
        relative_path = path
    return relative_path.as_posix()


def file_modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def discover_session_files(
    sessions_dir: Path, *, include_ended_at: bool = False
) -> list[SessionFile]:
    if not sessions_dir.exists():
        return []

    paths = discover_session_paths(sessions_dir)
    session_files = []
    for path in paths:
        metadata = read_session_file_metadata(path, include_ended_at=include_ended_at)
        session_files.append(
            SessionFile(
                path=path,
                relative_path=format_session_file_path(path, sessions_dir),
                session_id=metadata.identity.session_id,
                started_at=metadata.started_at,
                ended_at=metadata.ended_at,
                session_id_is_canonical=metadata.identity.is_canonical,
                identity_warning=metadata.identity.warning,
                identity_status=metadata.identity.status,
                modified_at=file_modified_at(path),
            )
        )
    return session_files


def discover_session_paths(sessions_dir: Path) -> list[Path]:
    if not sessions_dir.exists():
        return []
    return sorted(candidate for candidate in sessions_dir.rglob("*.jsonl") if candidate.is_file())
