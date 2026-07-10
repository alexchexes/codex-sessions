import json
import shutil
import tempfile
import zipfile
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from codex_sessions.codex.state import (
    CodexStateError,
    StateCacheBackup,
    backup_dir_for,
    backup_file,
    backup_label,
    backup_session_index,
    remove_backup_dir_if_empty,
    reset_codex_state_cache,
    restore_file_backup,
    restore_session_index_backup,
    temp_path_for,
)
from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.errors import CliError
from codex_sessions.search.sessions import render_search_line_groups
from codex_sessions.sessions.cache import (
    file_fingerprint_from_session_cache,
    prune_missing_session_cache_entries,
    read_session_cache,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.documents import (
    SearchDocument,
    infer_title_from_message,
    inferred_thread_name,
    is_session_activity_record,
)
from codex_sessions.sessions.files import (
    SessionFile,
    SessionIdentity,
    discover_session_files,
    resolve_session_identity,
    session_id_from_path,
)
from codex_sessions.sessions.index import (
    format_session_index_timestamp,
    is_session_id,
    matching_session_index_records,
    normalize_session_id,
    resolve_session_index_record,
    session_index_record_id,
    session_index_record_thread_name,
    session_index_records,
    write_session_index_records,
)
from codex_sessions.sessions.manifest import (
    MANIFEST_FILENAME,
    ExportManifestEntry,
    ImportManifestEntry,
    export_manifest_bytes,
    file_fingerprint_from_bytes,
    manifest_path_key,
    read_directory_manifest,
    read_zip_manifest,
)
from codex_sessions.sessions.rollout import (
    ExportFailure,
    ExportSessionPlan,
    ExportSessionResult,
    ExportSessionsPlan,
    ExportSessionsResult,
    FileFingerprint,
    ImportConflict,
    ImportDivergedConflict,
    ImportDivergenceRecordPreview,
    ImportDuplicateSession,
    ImportFailure,
    ImportSessionPlan,
    ImportSessionResult,
    ImportSessionSide,
    ImportSessionsPlan,
    ImportSessionsResult,
    ImportSkippedHistory,
    ImportSkippedSession,
    SyncSessionsPlan,
    SyncSessionsResult,
    export_title_slug,
    file_fingerprint,
    format_fingerprint,
    output_arg_looks_like_directory,
    read_rollout_records,
    renamed_rollout_records,
    resolve_export_output_path,
    rollout_filename_date,
    thread_name_updated_matches_session,
    thread_name_updated_name,
    write_rollout_records,
)
from codex_sessions.sessions.rollout_history import (
    RolloutHistoryComparison,
    RolloutHistoryRelation,
    compare_rollout_histories,
)

EXPORT_OUTPUT_FILE = "file"
EXPORT_OUTPUT_DIRECTORY = "directory"
EXPORT_OUTPUT_ZIP = "zip"
MAX_DIVERGENCE_PREVIEW_LINES = 3
MAX_DIVERGENCE_PREVIEW_LINE_CHARS = 180


@dataclass(frozen=True)
class ExportSessionCandidate:
    source_path: Path
    session_id: str
    thread_name: str
    document: SearchDocument


@dataclass(frozen=True)
class ImportSourceOutcome:
    session_id: str
    source_path: Path
    value: (
        ImportSessionPlan
        | ImportSkippedSession
        | ImportSkippedHistory
        | ImportConflict
        | ImportDivergedConflict
    )


@dataclass(frozen=True)
class ImportSourceBundle:
    paths: tuple[Path, ...]
    source_fingerprints: Mapping[Path, FileFingerprint]
    warnings: tuple[str, ...] = ()


@dataclass
class LocalFingerprintCache:
    cache_path: Path
    entries: dict[str, Any]
    dirty: bool = False


class ImportSkippedIdentical(CliError):
    def __init__(
        self,
        *,
        source_path: Path,
        existing_path: Path,
        session_id: str,
        fingerprint: FileFingerprint,
    ) -> None:
        self.source_path = source_path
        self.existing_path = existing_path
        self.session_id = session_id
        self.fingerprint = fingerprint
        super().__init__(
            "Session already imported with identical rollout file: "
            f"{existing_path} ({format_fingerprint(fingerprint)})"
        )


class ImportRolloutConflict(CliError):
    def __init__(
        self,
        *,
        source_path: Path,
        existing_path: Path,
        session_id: str,
        source_fingerprint: FileFingerprint,
        existing_fingerprint: FileFingerprint,
        source_side: ImportSessionSide,
        existing_side: ImportSessionSide,
    ) -> None:
        self.source_path = source_path
        self.existing_path = existing_path
        self.session_id = session_id
        self.source_fingerprint = source_fingerprint
        self.existing_fingerprint = existing_fingerprint
        self.source_side = source_side
        self.existing_side = existing_side
        existing_fingerprint_text = (
            format_fingerprint(existing_fingerprint) if existing_fingerprint else "UNKNOWN"
        )
        super().__init__(
            "Session already imported, but rollout file differs. "
            f"Existing: {existing_path} ({existing_fingerprint_text}); "
            f"import: {source_path} ({format_fingerprint(source_fingerprint)})."
        )


def load_local_fingerprint_cache(codex_home: Path) -> LocalFingerprintCache:
    cache_path = session_cache_path(codex_home)
    return LocalFingerprintCache(cache_path=cache_path, entries=read_session_cache(cache_path))


def local_rollout_fingerprint(path: Path, cache: LocalFingerprintCache | None) -> FileFingerprint:
    if cache is None:
        return file_fingerprint(path)
    fingerprint, _, updated = file_fingerprint_from_session_cache(path, cache.entries)
    cache.dirty = cache.dirty or updated
    return fingerprint


def source_rollout_fingerprint(
    path: Path,
    source_fingerprints: Mapping[Path, FileFingerprint] | None,
) -> FileFingerprint:
    if source_fingerprints is not None:
        fingerprint = source_fingerprints.get(path)
        if fingerprint is not None:
            return fingerprint
    return file_fingerprint(path)


def search_document_for_path(
    path: Path,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> SearchDocument:
    resolved_path = path.resolve()
    if document_cache is not None:
        document = document_cache.get(resolved_path)
        if document is not None:
            return document
    document = build_transfer_document(resolved_path)
    if document_cache is not None:
        document_cache[resolved_path] = document
    return document


def build_transfer_document(input_path: Path) -> SearchDocument:
    identity: SessionIdentity | None = None
    session_id: str | None = None
    thread_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_activity_at: datetime | None = None
    first_user_line: str | None = None
    first_codex_line: str | None = None

    for _, raw_record in iter_jsonl_objects(input_path):
        if identity is None:
            identity = resolve_session_identity(input_path, raw_record)
            session_id = identity.session_id
        record_timestamp = parse_timestamp(raw_record.get("timestamp"))
        if record_timestamp is not None:
            ended_at = record_timestamp
            if is_session_activity_record(raw_record):
                last_activity_at = record_timestamp

        payload = raw_record.get("payload")
        if started_at is None:
            started_at = record_timestamp
            if started_at is None and isinstance(payload, dict):
                started_at = parse_timestamp(payload.get("timestamp"))
        if raw_record.get("type") == "event_msg" and isinstance(payload, dict):
            if thread_name_updated_matches_session(payload, session_id):
                event_thread_name = thread_name_updated_name(payload)
                if event_thread_name:
                    thread_name = event_thread_name
        if first_user_line is None or first_codex_line is None:
            for group, lines in render_search_line_groups(raw_record):
                if group != "visible":
                    continue
                for line in lines:
                    if (
                        first_user_line is None
                        and line.startswith("User: ")
                        and infer_title_from_message(line[len("User: ") :])
                    ):
                        first_user_line = line
                    elif (
                        first_codex_line is None
                        and line.startswith("Codex: ")
                        and infer_title_from_message(line[len("Codex: ") :])
                    ):
                        first_codex_line = line

    if identity is None:
        identity = resolve_session_identity(input_path, None)
        session_id = identity.session_id

    return SearchDocument(
        session_id=session_id,
        thread_name=thread_name,
        started_at=started_at,
        ended_at=ended_at,
        last_activity_at=last_activity_at,
        visible_lines=tuple(
            line for line in (first_user_line, first_codex_line) if line is not None
        ),
        metadata_lines=(),
        tool_input_lines=(),
        tool_output_lines=(),
        session_id_is_canonical=identity.is_canonical,
        identity_warning=identity.warning,
        identity_status=identity.status,
    )


def require_canonical_session_id(document: SearchDocument, path: Path) -> str:
    if document.session_id is not None and document.session_id_is_canonical:
        return document.session_id
    detail = document.identity_warning or (
        "record 1 must be session_meta with a valid UUID payload.id"
    )
    raise CliError(f"Invalid Codex rollout identity: {path}: {detail}")


def write_local_fingerprint_cache(cache: LocalFingerprintCache | None) -> None:
    if cache is None or not cache.dirty:
        return
    prune_missing_session_cache_entries(cache.entries)
    try:
        write_session_cache(cache.cache_path, cache.entries)
    except OSError:
        # Fingerprint cache writes are an optimization; import planning must stay functional.
        return


def import_session_side(
    path: Path,
    document: SearchDocument,
    fingerprint: FileFingerprint,
    *,
    session_id: str,
) -> ImportSessionSide:
    return ImportSessionSide(
        path=path,
        session_id=session_id,
        thread_name=inferred_thread_name(document),
        started_at=document.started_at,
        ended_at=document.ended_at,
        fingerprint=fingerprint,
    )


def compact_divergence_preview_line(line: str) -> str:
    normalized = " ".join(line.split())
    if len(normalized) <= MAX_DIVERGENCE_PREVIEW_LINE_CHARS:
        return normalized
    return f"{normalized[: MAX_DIVERGENCE_PREVIEW_LINE_CHARS - 3].rstrip()}..."


def divergence_record_preview(
    record: dict[str, Any] | None,
) -> ImportDivergenceRecordPreview | None:
    if record is None:
        return None

    rendered_groups = render_search_line_groups(record)
    lines: list[str] = []
    # Divergence output should prefer what the user would recognize before raw metadata.
    for preferred_group in ("visible", "tool_inputs", "tool_outputs", "metadata"):
        for group, group_lines in rendered_groups:
            if group == preferred_group:
                lines.extend(group_lines)
        if lines:
            break

    if not lines:
        payload = record.get("payload")
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        lines.append(
            "record: "
            + " / ".join(
                str(value)
                for value in (record.get("type"), payload_type)
                if isinstance(value, str) and value
            )
        )

    return ImportDivergenceRecordPreview(
        record_type=str(record.get("type") or "record"),
        timestamp=parse_timestamp(record.get("timestamp")),
        lines=tuple(
            compact_divergence_preview_line(line) for line in lines[:MAX_DIVERGENCE_PREVIEW_LINES]
        ),
    )


def import_target_date(source_path: Path, document: SearchDocument) -> tuple[str, str, str]:
    filename_date = rollout_filename_date(source_path)
    if filename_date is not None:
        # Exported and native rollout names are authoritative for Codex's YYYY/MM/DD layout.
        return filename_date
    if document.started_at is None:
        raise CliError(
            f"Cannot infer session date from rollout filename or timestamps: {source_path}"
        )
    local_started_at = document.started_at.astimezone()
    return (
        f"{local_started_at.year:04d}",
        f"{local_started_at.month:02d}",
        f"{local_started_at.day:02d}",
    )


def import_target_filename(source_path: Path, document: SearchDocument) -> str:
    if document.session_id is None:
        raise CliError(f"Cannot infer session id from rollout: {source_path}")
    filename_id = session_id_from_path(source_path)
    if (
        source_path.name.startswith("rollout-")
        and filename_id is not None
        and normalize_session_id(filename_id) == normalize_session_id(document.session_id)
    ):
        return source_path.name
    if document.started_at is None:
        raise CliError(
            f"Cannot infer rollout filename from source name or timestamps: {source_path}"
        )
    local_started_at = document.started_at.astimezone()
    timestamp = local_started_at.strftime("%Y-%m-%dT%H-%M-%S")
    return f"rollout-{timestamp}-{document.session_id}.jsonl"


def import_target_path(source_path: Path, sessions_dir: Path, document: SearchDocument) -> Path:
    year, month, day = import_target_date(source_path, document)
    return sessions_dir / year / month / day / import_target_filename(source_path, document)


def session_files_by_normalized_id(
    session_files: Iterable[SessionFile],
) -> dict[str, list[SessionFile]]:
    session_files_by_id: dict[str, list[SessionFile]] = {}
    for session_file in session_files:
        if session_file.session_id:
            session_files_by_id.setdefault(
                normalize_session_id(session_file.session_id), []
            ).append(session_file)
    return session_files_by_id


def existing_session_files_for_id(
    session_id: str,
    sessions_dir: Path,
    *,
    session_files_by_id: Mapping[str, Sequence[SessionFile]] | None = None,
) -> list[SessionFile]:
    normalized_id = normalize_session_id(session_id)
    if session_files_by_id is not None:
        return list(session_files_by_id.get(normalized_id, ()))
    return [
        session_file
        for session_file in discover_session_files(sessions_dir)
        if session_file.session_id
        and normalize_session_id(session_file.session_id) == normalized_id
    ]


def existing_index_record_for_id(
    records: Sequence[Any], session_id: str
) -> tuple[int, dict[str, Any]] | None:
    normalized_id = normalize_session_id(session_id)
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        record_id = session_index_record_id(record)
        if record_id and normalize_session_id(record_id) == normalized_id:
            return index, record
    return None


def first_existing_rollout_for_import(
    existing_files: Sequence[SessionFile], target_path: Path
) -> Path | None:
    if target_path.exists():
        return target_path
    return existing_files[0].path if existing_files else None


def prepare_import_rollout_records(
    source_path: Path, session_id: str, thread_name: str
) -> tuple[list[dict[str, Any]], bool]:
    records = read_rollout_records(source_path)
    updated_records, _, changed = renamed_rollout_records(records, session_id, thread_name)
    return updated_records, changed


def plan_bare_rollout_import(
    source_path: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    name: str | None = None,
    local_fingerprint_cache: LocalFingerprintCache | None = None,
    source_fingerprints: Mapping[Path, FileFingerprint] | None = None,
    local_session_files_by_id: Mapping[str, Sequence[SessionFile]] | None = None,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ImportSessionPlan:
    expanded_source_path = source_path.expanduser().resolve()
    if not expanded_source_path.exists():
        raise CliError(f"Input file not found: {source_path}")
    if not expanded_source_path.is_file():
        raise CliError(f"Input path is not a file: {source_path}")

    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    document = search_document_for_path(expanded_source_path, document_cache)
    session_id = require_canonical_session_id(document, expanded_source_path)

    target_path = import_target_path(expanded_source_path, resolved_sessions_dir, document)
    source_fingerprint = source_rollout_fingerprint(expanded_source_path, source_fingerprints)
    existing_files = existing_session_files_for_id(
        session_id,
        resolved_sessions_dir,
        session_files_by_id=local_session_files_by_id,
    )
    existing_rollout_path = first_existing_rollout_for_import(existing_files, target_path)
    existing_rollout_fingerprint = (
        local_rollout_fingerprint(existing_rollout_path, local_fingerprint_cache)
        if existing_rollout_path is not None
        else None
    )

    if existing_rollout_path is not None:
        existing_document = search_document_for_path(existing_rollout_path, document_cache)
        require_canonical_session_id(existing_document, existing_rollout_path)
        if source_fingerprint == existing_rollout_fingerprint:
            raise ImportSkippedIdentical(
                source_path=expanded_source_path,
                existing_path=existing_rollout_path,
                session_id=session_id,
                fingerprint=source_fingerprint,
            )
        if existing_rollout_fingerprint is None:
            raise CliError(
                f"Could not fingerprint existing Codex rollout file: {existing_rollout_path}"
            )
        raise ImportRolloutConflict(
            source_path=expanded_source_path,
            existing_path=existing_rollout_path,
            session_id=session_id,
            source_fingerprint=source_fingerprint,
            existing_fingerprint=existing_rollout_fingerprint,
            source_side=import_session_side(
                expanded_source_path,
                document,
                source_fingerprint,
                session_id=session_id,
            ),
            existing_side=import_session_side(
                existing_rollout_path,
                existing_document,
                existing_rollout_fingerprint,
                session_id=session_id,
            ),
        )

    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_records = session_index_records(index_path) if index_path.exists() else []
    existing_index_match = existing_index_record_for_id(index_records, session_id)
    existing_index_thread_name = (
        session_index_record_thread_name(existing_index_match[1])
        if existing_index_match is not None
        else None
    )

    if name is not None:
        normalized_name = name.strip()
    elif existing_index_thread_name:
        normalized_name = existing_index_thread_name
    else:
        normalized_name = inferred_thread_name(document)
    if not normalized_name:
        raise CliError("Imported session title must not be empty.")

    if existing_index_match is None:
        index_action = "add"
    elif existing_index_thread_name != normalized_name and name is not None:
        index_action = "update"
    else:
        index_action = "keep"

    _, rollout_will_be_rewritten = prepare_import_rollout_records(
        expanded_source_path, session_id, normalized_name
    )

    return ImportSessionPlan(
        source_path=expanded_source_path,
        target_path=target_path,
        session_index_path=index_path,
        session_id=session_id,
        thread_name=normalized_name,
        started_at=document.started_at,
        ended_at=document.ended_at,
        index_action=index_action,
        existing_index_thread_name=existing_index_thread_name,
        source_fingerprint=source_fingerprint,
        rollout_will_be_rewritten=rollout_will_be_rewritten,
    )


def plan_fast_forward_rollout_import(
    source_path: Path,
    existing_path: Path,
    source_fingerprint: FileFingerprint,
    codex_home: Path,
    session_index_path: Path | None = None,
    *,
    name: str | None = None,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ImportSessionPlan:
    document = search_document_for_path(source_path, document_cache)
    session_id = require_canonical_session_id(document, source_path)
    existing_document = search_document_for_path(existing_path, document_cache)
    require_canonical_session_id(existing_document, existing_path)

    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_records = session_index_records(index_path) if index_path.exists() else []
    existing_index_match = existing_index_record_for_id(index_records, session_id)
    existing_index_thread_name = (
        session_index_record_thread_name(existing_index_match[1])
        if existing_index_match is not None
        else None
    )

    if name is not None:
        normalized_name = name.strip()
    elif document.thread_name:
        normalized_name = document.thread_name
    elif existing_index_thread_name:
        normalized_name = existing_index_thread_name
    else:
        normalized_name = existing_document.thread_name or inferred_thread_name(document)
    if not normalized_name:
        raise CliError("Imported session title must not be empty.")

    index_action = "add" if existing_index_match is None else "advance"
    _, rollout_will_be_rewritten = prepare_import_rollout_records(
        source_path, session_id, normalized_name
    )

    return ImportSessionPlan(
        source_path=source_path,
        target_path=existing_path,
        session_index_path=index_path,
        session_id=session_id,
        thread_name=normalized_name,
        started_at=document.started_at,
        ended_at=document.ended_at,
        index_action=index_action,
        existing_index_thread_name=existing_index_thread_name,
        source_fingerprint=source_fingerprint,
        rollout_will_be_rewritten=rollout_will_be_rewritten,
        replaces_existing_rollout=True,
    )


def session_index_record_for_import_plan(plan: ImportSessionPlan) -> dict[str, str]:
    return {
        "id": plan.session_id,
        "thread_name": plan.thread_name,
        "updated_at": format_session_index_timestamp(plan.ended_at or plan.started_at),
    }


def session_index_records_for_import(plan: ImportSessionPlan) -> list[Any]:
    records = (
        session_index_records(plan.session_index_path) if plan.session_index_path.exists() else []
    )
    return apply_import_plan_to_session_index_records(records, plan)


def apply_import_plan_to_session_index_records(
    records: Sequence[Any], plan: ImportSessionPlan
) -> list[Any]:
    existing_index_match = existing_index_record_for_id(records, plan.session_id)
    if plan.index_action == "add":
        if existing_index_match is not None:
            return list(records)
        return [*records, session_index_record_for_import_plan(plan)]
    if plan.index_action in {"update", "advance"}:
        if existing_index_match is None:
            raise CliError(f"No session_index.jsonl entry found for ID: {plan.session_id}")
        record_index, record = existing_index_match
        updated_records = list(records)
        updated_record = dict(record)
        updated_record["thread_name"] = plan.thread_name
        if plan.index_action == "advance":
            # Fast-forward imports are the only path that should advance updated_at.
            updated_record["updated_at"] = format_session_index_timestamp(
                plan.ended_at or plan.started_at
            )
        updated_records[record_index] = updated_record
        return updated_records
    return list(records)


def copy_or_rewrite_import_rollout(plan: ImportSessionPlan) -> None:
    plan.target_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.rollout_will_be_rewritten:
        records, _ = prepare_import_rollout_records(
            plan.source_path, plan.session_id, plan.thread_name
        )
        write_rollout_records(plan.target_path, records)
        return
    shutil.copy2(plan.source_path, plan.target_path)


def import_bare_rollout(
    source_path: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    name: str | None = None,
    reset_state_cache: bool = True,
) -> ImportSessionResult:
    local_fingerprint_cache = load_local_fingerprint_cache(codex_home)
    plan = plan_bare_rollout_import(
        source_path=source_path,
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        name=name,
        local_fingerprint_cache=local_fingerprint_cache,
    )
    write_local_fingerprint_cache(local_fingerprint_cache)
    index_changed = plan.index_action in {"add", "update"}
    updated_index_records = session_index_records_for_import(plan) if index_changed else None

    label = backup_label()
    backup_dir = backup_dir_for(codex_home, label)
    index_backup_path = (
        backup_session_index(plan.session_index_path, backup_dir) if index_changed else None
    )
    rollout_written = False
    attempted_rollout_path = False
    try:
        if index_changed:
            if updated_index_records is None:
                raise CliError("Could not prepare session_index.jsonl update.")
            plan.session_index_path.parent.mkdir(parents=True, exist_ok=True)
            write_session_index_records(plan.session_index_path, updated_index_records)
        attempted_rollout_path = True
        copy_or_rewrite_import_rollout(plan)
        rollout_written = True
    except (CliError, CodexStateError, OSError) as exc:
        try:
            if rollout_written or attempted_rollout_path:
                restore_file_backup(plan.target_path, None)
            if index_changed:
                restore_session_index_backup(plan.session_index_path, index_backup_path)
            remove_backup_dir_if_empty(backup_dir)
        except OSError as restore_exc:
            raise CliError(
                f"{exc} Also failed to restore Codex session files from backup: {restore_exc}"
            ) from restore_exc
        raise CliError(
            f"{exc} Rolled back imported Codex session files. Close all Codex sessions and retry."
        ) from exc

    state_cache_backups: tuple[StateCacheBackup, ...] = ()
    state_cache_reset_error = None
    if reset_state_cache:
        try:
            state_cache_backups = reset_codex_state_cache(codex_home, backup_dir)
        except (CodexStateError, OSError) as exc:
            state_cache_reset_error = str(exc)
            remove_backup_dir_if_empty(backup_dir)

    return ImportSessionResult(
        plan=plan,
        session_index_backup_path=index_backup_path,
        state_cache_backups=state_cache_backups,
        state_cache_reset_error=state_cache_reset_error,
        state_cache_reset_skipped=not reset_state_cache,
    )


def zip_member_output_path(temp_dir: Path, index: int, member_name: str) -> Path:
    member_path = PurePosixPath(member_name)
    name = member_path.name or f"rollout-{index}.jsonl"
    # Use a numbered folder, not a filename prefix, so rollout-* basename parsing still works.
    return temp_dir / f"{index:05d}" / name


def manifest_fingerprints_for_paths(
    paths_by_manifest_key: Mapping[str, Path],
    manifest_entries: Mapping[str, ImportManifestEntry],
) -> tuple[dict[Path, FileFingerprint], tuple[str, ...]]:
    source_fingerprints: dict[Path, FileFingerprint] = {}
    warnings: list[str] = []
    for manifest_key, path in paths_by_manifest_key.items():
        entry = manifest_entries.get(manifest_key)
        if entry is None:
            continue
        try:
            actual_fingerprint = file_fingerprint(path)
        except OSError as exc:
            warnings.append(
                f"Could not validate export manifest fingerprint for {path}: {exc}. "
                "Falling back to import-time hashing."
            )
            continue
        source_fingerprints[path.resolve()] = actual_fingerprint
        if actual_fingerprint != entry.fingerprint:
            warnings.append(
                f"Ignored export manifest fingerprint for {manifest_key}: "
                "file contents do not match the manifest."
            )
    return source_fingerprints, tuple(warnings)


def directory_import_source_bundle(source_dir: Path) -> ImportSourceBundle:
    paths = tuple(sorted(path for path in source_dir.rglob("*.jsonl") if path.is_file()))
    manifest_entries, manifest_warnings = read_directory_manifest(source_dir)
    paths_by_manifest_key = {
        manifest_path_key(path.relative_to(source_dir)): path for path in paths
    }
    source_fingerprints, fingerprint_warnings = manifest_fingerprints_for_paths(
        paths_by_manifest_key,
        manifest_entries,
    )
    return ImportSourceBundle(
        paths=paths,
        source_fingerprints=source_fingerprints,
        warnings=(*manifest_warnings, *fingerprint_warnings),
    )


def extract_zip_rollout_sources(zip_path: Path, temp_dir: Path) -> ImportSourceBundle:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            manifest_entries, manifest_warnings = read_zip_manifest(archive, zip_path)
            paths = []
            paths_by_manifest_key: dict[str, Path] = {}
            for index, member in enumerate(archive.infolist(), start=1):
                if member.is_dir() or not member.filename.lower().endswith(".jsonl"):
                    continue
                output_path = zip_member_output_path(temp_dir, index, member.filename)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(archive.read(member))
                paths.append(output_path)
                paths_by_manifest_key[manifest_path_key(member.filename)] = output_path
    except zipfile.BadZipFile as exc:
        raise CliError(f"Import zip is not a valid zip archive: {zip_path}") from exc
    source_fingerprints, fingerprint_warnings = manifest_fingerprints_for_paths(
        paths_by_manifest_key,
        manifest_entries,
    )
    return ImportSourceBundle(
        paths=tuple(paths),
        source_fingerprints=source_fingerprints,
        warnings=(*manifest_warnings, *fingerprint_warnings),
    )


@contextmanager
def import_rollout_source_bundle(source_path: Path) -> Generator[ImportSourceBundle]:
    expanded_source_path = source_path.expanduser().resolve()
    if not expanded_source_path.exists():
        raise CliError(f"Input file not found: {source_path}")
    if expanded_source_path.is_dir():
        bundle = directory_import_source_bundle(expanded_source_path)
        if not bundle.paths:
            raise CliError(f"No rollout JSONL files found in import directory: {source_path}")
        yield bundle
        return
    if not expanded_source_path.is_file():
        raise CliError(f"Input path is not a file or directory: {source_path}")
    if expanded_source_path.suffix.lower() != ".zip":
        yield ImportSourceBundle(
            paths=(expanded_source_path,),
            source_fingerprints={},
        )
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        bundle = extract_zip_rollout_sources(expanded_source_path, Path(tmpdir))
        if not bundle.paths:
            raise CliError(f"No rollout JSONL files found in import zip: {source_path}")
        yield bundle


def import_history_skip(
    conflict: ImportRolloutConflict, comparison: RolloutHistoryComparison
) -> ImportSkippedHistory:
    if comparison.common_comparable_records is None:
        raise CliError(f"Could not compare imported rollout history: {conflict.source_path}")
    return ImportSkippedHistory(
        source_path=conflict.source_path,
        existing_path=conflict.existing_path,
        session_id=conflict.session_id,
        source_side=conflict.source_side,
        existing_side=conflict.existing_side,
        common_comparable_records=comparison.common_comparable_records,
        existing_tail_comparable_records=comparison.local_tail_comparable_records,
        incoming_tail_comparable_records=comparison.incoming_tail_comparable_records,
    )


def import_diverged_conflict(
    conflict: ImportRolloutConflict,
    existing_fingerprint: FileFingerprint,
    comparison: RolloutHistoryComparison,
) -> ImportDivergedConflict:
    if comparison.common_comparable_records is None:
        raise CliError(f"Could not compare imported rollout history: {conflict.source_path}")
    return ImportDivergedConflict(
        source_path=conflict.source_path,
        existing_path=conflict.existing_path,
        session_id=conflict.session_id,
        source_fingerprint=conflict.source_fingerprint,
        existing_fingerprint=existing_fingerprint,
        source_side=conflict.source_side,
        existing_side=conflict.existing_side,
        source_divergence_preview=divergence_record_preview(comparison.incoming_divergence_record),
        existing_divergence_preview=divergence_record_preview(comparison.local_divergence_record),
        common_comparable_records=comparison.common_comparable_records,
        existing_tail_comparable_records=comparison.local_tail_comparable_records,
        incoming_tail_comparable_records=comparison.incoming_tail_comparable_records,
    )


def merge_conflicting_rollout_import(
    conflict: ImportRolloutConflict,
    codex_home: Path,
    session_index_path: Path | None,
    *,
    name: str | None,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ImportSessionPlan | ImportSkippedHistory | ImportDivergedConflict:
    existing_fingerprint = conflict.existing_fingerprint or file_fingerprint(conflict.existing_path)
    comparison = compare_rollout_histories(
        conflict.existing_path,
        conflict.source_path,
        local_session_id=conflict.session_id,
        incoming_session_id=conflict.session_id,
        local_fingerprint=existing_fingerprint,
        incoming_fingerprint=conflict.source_fingerprint,
    )
    if comparison.relation == RolloutHistoryRelation.EQUIVALENT:
        return import_history_skip(conflict, comparison)
    if comparison.relation == RolloutHistoryRelation.LOCAL_AHEAD:
        return import_history_skip(conflict, comparison)
    if comparison.relation == RolloutHistoryRelation.INCOMING_AHEAD:
        return plan_fast_forward_rollout_import(
            source_path=conflict.source_path,
            existing_path=conflict.existing_path,
            source_fingerprint=conflict.source_fingerprint,
            codex_home=codex_home,
            session_index_path=session_index_path,
            name=name,
            document_cache=document_cache,
        )
    return import_diverged_conflict(conflict, existing_fingerprint, comparison)


def split_import_outcomes(
    outcomes: Sequence[ImportSourceOutcome],
) -> tuple[
    list[ImportSessionPlan],
    list[ImportSessionPlan],
    list[ImportSkippedSession],
    list[ImportSkippedHistory],
    list[ImportSkippedHistory],
    list[ImportDuplicateSession],
    list[ImportConflict],
    list[ImportDivergedConflict],
]:
    outcomes_by_id: dict[str, list[ImportSourceOutcome]] = {}
    for outcome in outcomes:
        outcomes_by_id.setdefault(normalize_session_id(outcome.session_id), []).append(outcome)

    import_plans: list[ImportSessionPlan] = []
    fast_forward_plans: list[ImportSessionPlan] = []
    skipped: list[ImportSkippedSession] = []
    skipped_equivalent: list[ImportSkippedHistory] = []
    skipped_local_ahead: list[ImportSkippedHistory] = []
    duplicates: list[ImportDuplicateSession] = []
    conflicts: list[ImportConflict] = []
    diverged: list[ImportDivergedConflict] = []

    for grouped_outcomes in outcomes_by_id.values():
        if len(grouped_outcomes) > 1:
            # Duplicate IDs in one import are ambiguous, so all matching inputs are skipped.
            duplicates.append(
                ImportDuplicateSession(
                    session_id=grouped_outcomes[0].session_id,
                    source_paths=tuple(outcome.source_path for outcome in grouped_outcomes),
                )
            )
            continue

        value = grouped_outcomes[0].value
        if isinstance(value, ImportSessionPlan):
            if value.replaces_existing_rollout:
                fast_forward_plans.append(value)
            else:
                import_plans.append(value)
        elif isinstance(value, ImportSkippedSession):
            skipped.append(value)
        elif isinstance(value, ImportSkippedHistory):
            if value.incoming_tail_comparable_records:
                raise CliError(f"Unexpected incoming tail for skipped import: {value.source_path}")
            if value.existing_tail_comparable_records:
                skipped_local_ahead.append(value)
            else:
                skipped_equivalent.append(value)
        elif isinstance(value, ImportDivergedConflict):
            diverged.append(value)
        else:
            conflicts.append(value)

    return (
        import_plans,
        fast_forward_plans,
        skipped,
        skipped_equivalent,
        skipped_local_ahead,
        duplicates,
        conflicts,
        diverged,
    )


def plan_import_source_paths(
    source_paths: Sequence[Path],
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    name: str | None = None,
    merge: bool = False,
    persist_local_fingerprint_cache: bool = True,
    source_fingerprints: Mapping[Path, FileFingerprint] | None = None,
    document_cache: dict[Path, SearchDocument] | None = None,
    warnings: Sequence[str] = (),
) -> ImportSessionsPlan:
    if name is not None and len(source_paths) != 1:
        raise CliError("--name can only be used when importing one rollout file.")

    local_fingerprint_cache = load_local_fingerprint_cache(codex_home)
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    local_session_files_by_id = session_files_by_normalized_id(
        discover_session_files(resolved_sessions_dir)
    )
    outcomes: list[ImportSourceOutcome] = []
    failures: list[ImportFailure] = []
    for source_path in source_paths:
        try:
            plan = plan_bare_rollout_import(
                source_path=source_path,
                codex_home=codex_home,
                session_index_path=session_index_path,
                sessions_dir=sessions_dir,
                name=name,
                local_fingerprint_cache=local_fingerprint_cache,
                source_fingerprints=source_fingerprints,
                local_session_files_by_id=local_session_files_by_id,
                document_cache=document_cache,
            )
            outcomes.append(
                ImportSourceOutcome(
                    session_id=plan.session_id,
                    source_path=plan.source_path,
                    value=plan,
                )
            )
        except ImportSkippedIdentical as exc:
            skipped_session = ImportSkippedSession(
                source_path=exc.source_path,
                existing_path=exc.existing_path,
                session_id=exc.session_id,
                fingerprint=exc.fingerprint,
            )
            outcomes.append(
                ImportSourceOutcome(
                    session_id=skipped_session.session_id,
                    source_path=skipped_session.source_path,
                    value=skipped_session,
                )
            )
        except ImportRolloutConflict as exc:
            outcome: (
                ImportSessionPlan | ImportSkippedHistory | ImportConflict | ImportDivergedConflict
            )
            if merge:
                outcome = merge_conflicting_rollout_import(
                    exc,
                    codex_home=codex_home,
                    session_index_path=session_index_path,
                    name=name,
                    document_cache=document_cache,
                )
            else:
                outcome = ImportConflict(
                    source_path=exc.source_path,
                    existing_path=exc.existing_path,
                    session_id=exc.session_id,
                    source_fingerprint=exc.source_fingerprint,
                    existing_fingerprint=exc.existing_fingerprint,
                    source_side=exc.source_side,
                    existing_side=exc.existing_side,
                )
            outcomes.append(
                ImportSourceOutcome(
                    session_id=outcome.session_id,
                    source_path=outcome.source_path,
                    value=outcome,
                )
            )
        except (CliError, ValueError, OSError) as exc:
            failures.append(ImportFailure(source_path=source_path, message=str(exc)))

    (
        import_plans,
        fast_forward_plans,
        skipped,
        skipped_equivalent,
        skipped_local_ahead,
        duplicates,
        conflicts,
        diverged,
    ) = split_import_outcomes(outcomes)
    if persist_local_fingerprint_cache:
        write_local_fingerprint_cache(local_fingerprint_cache)
    return ImportSessionsPlan(
        import_plans=tuple(import_plans),
        fast_forward_plans=tuple(fast_forward_plans),
        skipped=tuple(skipped),
        skipped_equivalent=tuple(skipped_equivalent),
        skipped_local_ahead=tuple(skipped_local_ahead),
        duplicates=tuple(duplicates),
        conflicts=tuple(conflicts),
        diverged=tuple(diverged),
        failures=tuple(failures),
        warnings=tuple(warnings),
    )


def plan_sessions_import(
    source_path: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    name: str | None = None,
    merge: bool = False,
    persist_local_fingerprint_cache: bool = True,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ImportSessionsPlan:
    with import_rollout_source_bundle(source_path) as source_bundle:
        return plan_import_source_paths(
            source_paths=source_bundle.paths,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            name=name,
            merge=merge,
            persist_local_fingerprint_cache=persist_local_fingerprint_cache,
            source_fingerprints=source_bundle.source_fingerprints,
            document_cache=document_cache,
            warnings=source_bundle.warnings,
        )


def session_index_records_for_import_plans(plans: Sequence[ImportSessionPlan]) -> list[Any]:
    if not plans:
        return []
    index_path = plans[0].session_index_path
    records = session_index_records(index_path) if index_path.exists() else []
    for plan in plans:
        records = apply_import_plan_to_session_index_records(records, plan)
    return records


def import_session_plans(
    plans: Sequence[ImportSessionPlan],
    codex_home: Path,
    *,
    reset_state_cache: bool = True,
) -> tuple[Path | None, tuple[Path, ...], tuple[StateCacheBackup, ...], str | None]:
    if not plans:
        return None, (), (), None

    index_path = plans[0].session_index_path
    index_changed = any(plan.index_action in {"add", "update", "advance"} for plan in plans)
    updated_index_records = session_index_records_for_import_plans(plans) if index_changed else None

    label = backup_label()
    backup_dir = backup_dir_for(codex_home, label)
    index_backup_path = backup_session_index(index_path, backup_dir) if index_changed else None
    rollout_backups = {
        plan.target_path: backup_file(plan.target_path, backup_dir)
        for plan in plans
        if plan.replaces_existing_rollout
    }
    attempted_paths: list[Path] = []
    try:
        if index_changed:
            if updated_index_records is None:
                raise CliError("Could not prepare session_index.jsonl update.")
            index_path.parent.mkdir(parents=True, exist_ok=True)
            write_session_index_records(index_path, updated_index_records)
        for plan in plans:
            attempted_paths.append(plan.target_path)
            copy_or_rewrite_import_rollout(plan)
    except (CliError, CodexStateError, OSError) as exc:
        try:
            for path in attempted_paths:
                restore_file_backup(path, rollout_backups.get(path))
            if index_changed:
                restore_session_index_backup(index_path, index_backup_path)
            remove_backup_dir_if_empty(backup_dir)
        except OSError as restore_exc:
            raise CliError(
                f"{exc} Also failed to restore Codex session files from backup: {restore_exc}"
            ) from restore_exc
        raise CliError(
            f"{exc} Rolled back imported Codex session files. Close all Codex sessions and retry."
        ) from exc

    state_cache_backups: tuple[StateCacheBackup, ...] = ()
    state_cache_reset_error = None
    if reset_state_cache:
        try:
            state_cache_backups = reset_codex_state_cache(codex_home, backup_dir)
        except (CodexStateError, OSError) as exc:
            state_cache_reset_error = str(exc)
            remove_backup_dir_if_empty(backup_dir)

    return (
        index_backup_path,
        tuple(backup for backup in rollout_backups.values() if backup is not None),
        state_cache_backups,
        state_cache_reset_error,
    )


def import_sessions(
    source_path: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    name: str | None = None,
    merge: bool = False,
    reset_state_cache: bool = True,
) -> ImportSessionsResult:
    with import_rollout_source_bundle(source_path) as source_bundle:
        document_cache: dict[Path, SearchDocument] = {}
        plan = plan_import_source_paths(
            source_paths=source_bundle.paths,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            name=name,
            merge=merge,
            persist_local_fingerprint_cache=True,
            source_fingerprints=source_bundle.source_fingerprints,
            document_cache=document_cache,
            warnings=source_bundle.warnings,
        )
        selected_plans = (*plan.import_plans, *plan.fast_forward_plans)
        if not selected_plans:
            return ImportSessionsResult(
                plan=plan,
                session_index_backup_path=None,
                rollout_backup_paths=(),
                state_cache_backups=(),
                state_cache_reset_error=None,
                state_cache_reset_skipped=False,
            )
        (
            session_index_backup_path,
            rollout_backup_paths,
            state_cache_backups,
            state_cache_reset_error,
        ) = import_session_plans(selected_plans, codex_home, reset_state_cache=reset_state_cache)
        return ImportSessionsResult(
            plan=plan,
            session_index_backup_path=session_index_backup_path,
            rollout_backup_paths=rollout_backup_paths,
            state_cache_backups=state_cache_backups,
            state_cache_reset_error=state_cache_reset_error,
            state_cache_reset_skipped=not reset_state_cache,
        )


def export_filename_date(source_path: Path, document: SearchDocument) -> str:
    filename_date = rollout_filename_date(source_path)
    if filename_date is not None:
        return "-".join(filename_date)
    if document.started_at is not None:
        return document.started_at.astimezone().strftime("%Y-%m-%d")
    return "unknown-date"


def default_export_filename(
    source_path: Path, document: SearchDocument, session_id: str, thread_name: str
) -> str:
    return (
        f"{export_filename_date(source_path, document)}--"
        f"{export_title_slug(thread_name)}--{session_id}.jsonl"
    )


def resolve_single_session_file_for_export(session_id: str, sessions_dir: Path) -> SessionFile:
    matches = existing_session_files_for_id(session_id, sessions_dir)
    if not matches:
        raise CliError(f"No Codex session file found for ID: {session_id}")
    if len(matches) > 1:
        rendered_matches = ", ".join(session_file.relative_path for session_file in matches)
        raise CliError(
            f"Multiple Codex session files found for ID {session_id}: {rendered_matches}"
        )
    return matches[0]


def export_session_index_record(
    target: str, index_path: Path
) -> tuple[str | None, dict[str, Any] | None]:
    if is_session_id(target):
        if not index_path.exists():
            return target, None
        records = session_index_records(index_path)
        match = existing_index_record_for_id(records, target)
        if match is None:
            return target, None
        record_id = session_index_record_id(match[1])
        return record_id or target, match[1]

    records = session_index_records(index_path)
    _, record = resolve_session_index_record(records, target)
    session_id = session_index_record_id(record)
    if session_id is None:
        raise CliError(f"Matched session_index.jsonl entry has no session id: {target}")
    return session_id, record


def export_filter_timestamp(value: str | None, option_name: str) -> datetime | None:
    if value is None:
        return None
    parsed = parse_timestamp(value)
    if parsed is None:
        raise CliError(f"Invalid {option_name} value: {value}")
    return parsed


def export_session_candidates(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    document_cache: dict[Path, SearchDocument] | None = None,
    failures: list[ExportFailure] | None = None,
) -> list[ExportSessionCandidate]:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    if not resolved_sessions_dir.exists():
        raise CliError(f"Sessions directory not found: {resolved_sessions_dir}")

    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_records = session_index_records(index_path) if index_path.exists() else []
    index_records_by_id: dict[str, dict[str, Any]] = {}
    for record in index_records:
        if not isinstance(record, dict):
            continue
        record_id = session_index_record_id(record)
        if record_id:
            index_records_by_id.setdefault(normalize_session_id(record_id), record)

    candidates: list[ExportSessionCandidate] = []
    candidates_by_id: dict[str, ExportSessionCandidate] = {}
    duplicate_sources_by_id: dict[str, list[Path]] = {}
    for session_file in discover_session_files(resolved_sessions_dir):
        source_path = session_file.path.resolve()
        try:
            document = search_document_for_path(source_path, document_cache)
        except (OSError, ValueError) as exc:
            if failures is None:
                raise
            failures.append(
                ExportFailure(
                    source_path=source_path,
                    message=str(exc),
                    started_at=session_file.started_at,
                    ended_at=session_file.ended_at,
                )
            )
            continue
        session_id = document.session_id
        if session_id is None:
            message = document.identity_warning or "Cannot infer session id from rollout"
            if failures is None:
                raise CliError(f"Cannot export rollout: {source_path}: {message}")
            failures.append(
                ExportFailure(
                    source_path=source_path,
                    message=message,
                    started_at=document.started_at,
                    ended_at=document.ended_at,
                )
            )
            continue
        normalized_id = normalize_session_id(session_id)
        index_record = index_records_by_id.get(normalized_id)
        index_thread_name = (
            session_index_record_thread_name(index_record) if index_record is not None else ""
        )
        thread_name = index_thread_name or inferred_thread_name(document)
        if not thread_name:
            raise CliError(f"Exported session title must not be empty: {source_path}")

        candidate = ExportSessionCandidate(
            source_path=source_path,
            session_id=session_id,
            thread_name=thread_name,
            document=document,
        )
        existing_candidate = candidates_by_id.get(normalized_id)
        if existing_candidate is not None:
            duplicate_sources_by_id.setdefault(
                normalized_id, [existing_candidate.source_path]
            ).append(source_path)
            continue
        candidates_by_id[normalized_id] = candidate
        candidates.append(candidate)

    if duplicate_sources_by_id:
        normalized_id, sources = next(iter(duplicate_sources_by_id.items()))
        rendered_sources = ", ".join(str(source) for source in sources)
        raise CliError(
            f"Multiple Codex session files found for ID {normalized_id}: {rendered_sources}"
        )

    return candidates


def candidate_by_session_id(
    candidates: Sequence[ExportSessionCandidate], session_id: str
) -> ExportSessionCandidate:
    normalized_id = normalize_session_id(session_id)
    for candidate in candidates:
        if normalize_session_id(candidate.session_id) == normalized_id:
            return candidate
    raise CliError(f"No Codex session file found for ID: {session_id}")


def candidate_for_export_selector(
    target: str,
    candidates: Sequence[ExportSessionCandidate],
    session_index_path: Path,
) -> ExportSessionCandidate:
    if is_session_id(target):
        return candidate_by_session_id(candidates, target)

    index_records = session_index_records(session_index_path) if session_index_path.exists() else []
    matches = matching_session_index_records(index_records, target)
    if len(matches) > 1:
        resolve_session_index_record(index_records, target)
    if len(matches) == 1:
        session_id = session_index_record_id(matches[0][1])
        if session_id is None:
            raise CliError(f"Matched session_index.jsonl entry has no session id: {target}")
        return candidate_by_session_id(candidates, session_id)

    title_matches = [candidate for candidate in candidates if candidate.thread_name == target]
    if len(title_matches) == 1:
        return title_matches[0]
    if not title_matches:
        raise CliError(f"No Codex session matched title: {target}")

    rendered_matches = ", ".join(candidate.session_id for candidate in title_matches)
    raise CliError(
        f"Multiple Codex sessions matched title {target!r}: {rendered_matches}. Re-run with one ID."
    )


def unique_export_candidates(
    candidates: Iterable[ExportSessionCandidate],
) -> list[ExportSessionCandidate]:
    unique_candidates: list[ExportSessionCandidate] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        normalized_id = normalize_session_id(candidate.session_id)
        if normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        unique_candidates.append(candidate)
    return unique_candidates


def export_candidate_updated_at(candidate: ExportSessionCandidate) -> datetime | None:
    return candidate.document.ended_at or candidate.document.started_at


def export_timestamp_values_match_filters(
    *,
    started_at: datetime | None,
    updated_at: datetime | None,
    started_after: datetime | None,
    started_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
) -> bool:
    if started_after is not None and (started_at is None or started_at < started_after):
        return False
    if started_before is not None and (started_at is None or started_at >= started_before):
        return False
    if updated_after is not None and (updated_at is None or updated_at < updated_after):
        return False
    return not (updated_before is not None and (updated_at is None or updated_at >= updated_before))


def export_candidate_matches_timestamp_filters(
    candidate: ExportSessionCandidate,
    *,
    started_after: datetime | None,
    started_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
) -> bool:
    return export_timestamp_values_match_filters(
        started_at=candidate.document.started_at,
        updated_at=export_candidate_updated_at(candidate),
        started_after=started_after,
        started_before=started_before,
        updated_after=updated_after,
        updated_before=updated_before,
    )


def export_failure_updated_at(failure: ExportFailure) -> datetime | None:
    return failure.ended_at or failure.started_at


def export_failure_matches_timestamp_filters(
    failure: ExportFailure,
    *,
    started_after: datetime | None,
    started_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
) -> bool:
    return export_timestamp_values_match_filters(
        started_at=failure.started_at,
        updated_at=export_failure_updated_at(failure),
        started_after=started_after,
        started_before=started_before,
        updated_after=updated_after,
        updated_before=updated_before,
    )


def selected_export_candidates(
    *,
    targets: Sequence[str],
    only: Sequence[str],
    exclude: Sequence[str],
    all_sessions: bool,
    codex_home: Path,
    session_index_path: Path,
    sessions_dir: Path,
    started_after: datetime | None,
    started_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
) -> tuple[list[ExportSessionCandidate], int, list[ExportFailure]]:
    if targets and only:
        raise CliError("Use either positional export targets or --only, not both.")
    if all_sessions and (targets or only):
        raise CliError("Use either --all or explicit export targets, not both.")

    has_timestamp_filters = any(
        timestamp is not None
        for timestamp in (started_after, started_before, updated_after, updated_before)
    )
    discovery_failures: list[ExportFailure] = []
    candidates = export_session_candidates(
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        failures=discovery_failures,
    )
    selector_targets = tuple(targets or only)
    if selector_targets:
        base_candidates = unique_export_candidates(
            candidate_for_export_selector(target, candidates, session_index_path)
            for target in selector_targets
        )
    elif all_sessions or has_timestamp_filters:
        base_candidates = candidates
    else:
        raise CliError("Export requires a session target, --only, --all, or a time filter.")

    excluded_ids = {
        normalize_session_id(
            candidate_for_export_selector(target, candidates, session_index_path).session_id
        )
        for target in exclude
    }

    selected_candidates = [
        candidate
        for candidate in base_candidates
        if normalize_session_id(candidate.session_id) not in excluded_ids
        and export_candidate_matches_timestamp_filters(
            candidate,
            started_after=started_after,
            started_before=started_before,
            updated_after=updated_after,
            updated_before=updated_before,
        )
    ]
    if selector_targets:
        selected_failures = []
    elif has_timestamp_filters:
        selected_failures = [
            failure
            for failure in discovery_failures
            if export_failure_matches_timestamp_filters(
                failure,
                started_after=started_after,
                started_before=started_before,
                updated_after=updated_after,
                updated_before=updated_before,
            )
        ]
    else:
        selected_failures = discovery_failures
    return (
        selected_candidates,
        len(base_candidates) - len(selected_candidates),
        selected_failures,
    )


def export_output_kind(output: Path | None, session_count: int) -> str:
    if output is None:
        if session_count == 1:
            return EXPORT_OUTPUT_FILE
        raise CliError("Bulk export requires --output/-o with a directory or .zip path.")

    expanded_output = output.expanduser()
    if expanded_output.exists() and expanded_output.is_dir():
        return EXPORT_OUTPUT_DIRECTORY
    if expanded_output.suffix.lower() == ".zip":
        return EXPORT_OUTPUT_ZIP
    if output_arg_looks_like_directory(output):
        return EXPORT_OUTPUT_DIRECTORY
    if session_count == 1:
        return EXPORT_OUTPUT_FILE
    raise CliError("Bulk export output must be a directory or .zip path.")


def export_candidate_default_filename(candidate: ExportSessionCandidate) -> str:
    return default_export_filename(
        candidate.source_path, candidate.document, candidate.session_id, candidate.thread_name
    )


def export_plan_for_candidate(
    candidate: ExportSessionCandidate,
    output_path: Path,
    *,
    force: bool,
    check_output_file: bool,
) -> ExportSessionPlan:
    source_path = candidate.source_path.resolve()
    rollout_will_be_rewritten = False
    if candidate.document.session_id_is_canonical:
        _, rollout_will_be_rewritten = prepare_import_rollout_records(
            source_path, candidate.session_id, candidate.thread_name
        )
    output_exists = output_path.exists() if check_output_file else False
    if check_output_file and output_exists:
        if output_path.resolve() == source_path:
            raise CliError(f"Export output path is the source rollout file: {output_path}")
        if not force:
            raise CliError(f"Output file already exists: {output_path}. Use --force to overwrite.")

    return ExportSessionPlan(
        source_path=source_path,
        output_path=output_path,
        session_id=candidate.session_id,
        thread_name=candidate.thread_name,
        started_at=candidate.document.started_at,
        ended_at=candidate.document.ended_at,
        rollout_will_be_rewritten=rollout_will_be_rewritten,
        overwrite=output_exists,
        identity_status=candidate.document.identity_status,
        warnings=(
            (f"{source_path}: {candidate.document.identity_warning}",)
            if candidate.document.identity_warning is not None
            else ()
        ),
    )


def export_output_path_for_candidate(
    candidate: ExportSessionCandidate, output: Path | None, output_kind: str
) -> Path:
    default_filename = export_candidate_default_filename(candidate)
    if output_kind == EXPORT_OUTPUT_FILE:
        return resolve_export_output_path(output, default_filename)
    if output_kind == EXPORT_OUTPUT_DIRECTORY:
        output_dir = output.expanduser() if output is not None else Path.cwd()
        return output_dir / default_filename
    return Path(default_filename)


def plan_sessions_export(
    *,
    targets: Sequence[str],
    codex_home: Path,
    output: Path | None = None,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    all_sessions: bool = False,
    only: Sequence[str] = (),
    exclude: Sequence[str] = (),
    started_after: str | None = None,
    started_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    force: bool = False,
) -> ExportSessionsPlan:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    index_path = session_index_path or codex_home / "session_index.jsonl"
    selected_candidates, filtered_out_count, failures = selected_export_candidates(
        targets=targets,
        only=only,
        exclude=exclude,
        all_sessions=all_sessions,
        codex_home=codex_home,
        session_index_path=index_path,
        sessions_dir=resolved_sessions_dir,
        started_after=export_filter_timestamp(started_after, "--started-after"),
        started_before=export_filter_timestamp(started_before, "--started-before"),
        updated_after=export_filter_timestamp(updated_after, "--updated-after"),
        updated_before=export_filter_timestamp(updated_before, "--updated-before"),
    )
    if not selected_candidates and not failures:
        raise CliError("No sessions matched export selection.")
    if output is None and (all_sessions or (not targets and not only)):
        raise CliError("Bulk export requires --output/-o with a directory or .zip path.")

    output_kind = export_output_kind(output, len(selected_candidates))
    output_path = output.expanduser() if output is not None else None
    if output_kind == EXPORT_OUTPUT_ZIP and output_path is not None:
        if output_path.exists() and output_path.is_dir():
            raise CliError(f"Export zip output is a directory: {output_path}")
        if output_path.exists() and not force:
            raise CliError(f"Output zip already exists: {output_path}. Use --force to replace.")

    plans = tuple(
        export_plan_for_candidate(
            candidate,
            export_output_path_for_candidate(candidate, output, output_kind),
            force=force,
            check_output_file=output_kind != EXPORT_OUTPUT_ZIP,
        )
        for candidate in selected_candidates
    )
    warnings = tuple(warning for plan in plans for warning in plan.warnings)

    return ExportSessionsPlan(
        session_plans=plans,
        output_kind=output_kind,
        output_path=output_path,
        force=force,
        filtered_out_count=filtered_out_count,
        failures=tuple(failures),
        warnings=warnings,
    )


def plan_session_export(
    target: str,
    codex_home: Path,
    output: Path | None = None,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    force: bool = False,
) -> ExportSessionPlan:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    if not resolved_sessions_dir.exists():
        raise CliError(f"Sessions directory not found: {resolved_sessions_dir}")

    index_path = session_index_path or codex_home / "session_index.jsonl"
    session_id, index_record = export_session_index_record(target, index_path)
    if session_id is None:
        raise CliError(f"Could not resolve session ID for export target: {target}")

    session_file = resolve_single_session_file_for_export(session_id, resolved_sessions_dir)
    source_path = session_file.path.resolve()
    document = search_document_for_path(source_path)
    resolved_session_id = document.session_id or session_file.session_id
    if resolved_session_id is None:
        raise CliError(f"Cannot infer session id from rollout: {source_path}")
    index_thread_name = (
        session_index_record_thread_name(index_record) if index_record is not None else ""
    )
    thread_name = index_thread_name or inferred_thread_name(document)
    if not thread_name:
        raise CliError("Exported session title must not be empty.")

    rollout_will_be_rewritten = False
    if document.session_id_is_canonical:
        _, rollout_will_be_rewritten = prepare_import_rollout_records(
            source_path, resolved_session_id, thread_name
        )
    output_path = resolve_export_output_path(
        output, default_export_filename(source_path, document, resolved_session_id, thread_name)
    )
    if output_path.exists():
        if output_path.resolve() == source_path:
            raise CliError(f"Export output path is the source rollout file: {output_path}")
        if not force:
            raise CliError(f"Output file already exists: {output_path}. Use --force to overwrite.")

    return ExportSessionPlan(
        source_path=source_path,
        output_path=output_path,
        session_id=resolved_session_id,
        thread_name=thread_name,
        started_at=document.started_at,
        ended_at=document.ended_at,
        rollout_will_be_rewritten=rollout_will_be_rewritten,
        overwrite=output_path.exists(),
        identity_status=document.identity_status,
        warnings=(
            (f"{source_path}: {document.identity_warning}",)
            if document.identity_warning is not None
            else ()
        ),
    )


def export_session(
    target: str,
    codex_home: Path,
    output: Path | None = None,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    force: bool = False,
) -> ExportSessionResult:
    plan = plan_session_export(
        target=target,
        codex_home=codex_home,
        output=output,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        force=force,
    )
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.rollout_will_be_rewritten:
        records, _ = prepare_import_rollout_records(
            plan.source_path, plan.session_id, plan.thread_name
        )
        write_rollout_records(plan.output_path, records)
    else:
        shutil.copy2(plan.source_path, plan.output_path)
    return ExportSessionResult(plan=plan)


def export_plan_jsonl_bytes(plan: ExportSessionPlan) -> bytes:
    if plan.rollout_will_be_rewritten:
        records, _ = prepare_import_rollout_records(
            plan.source_path, plan.session_id, plan.thread_name
        )
        text = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        )
        return text.encode("utf-8")
    return plan.source_path.read_bytes()


def export_manifest_entry(
    plan: ExportSessionPlan,
    relative_path: str,
    fingerprint: FileFingerprint,
) -> ExportManifestEntry:
    return ExportManifestEntry(
        relative_path=manifest_path_key(relative_path),
        session_id=plan.session_id,
        thread_name=plan.thread_name,
        started_at=plan.started_at,
        updated_at=plan.ended_at or plan.started_at,
        fingerprint=fingerprint,
    )


def write_export_plan_to_file(plan: ExportSessionPlan) -> None:
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.rollout_will_be_rewritten:
        records, _ = prepare_import_rollout_records(
            plan.source_path, plan.session_id, plan.thread_name
        )
        write_rollout_records(plan.output_path, records)
    else:
        shutil.copy2(plan.source_path, plan.output_path)


def write_export_manifest_to_directory(plan: ExportSessionsPlan) -> None:
    if plan.output_path is None:
        raise CliError("Directory export requires an output path.")
    output_dir = plan.output_path.expanduser()
    entries = []
    for session_plan in plan.session_plans:
        relative_path = session_plan.output_path.relative_to(output_dir).as_posix()
        entries.append(
            export_manifest_entry(
                session_plan,
                relative_path,
                file_fingerprint(session_plan.output_path),
            )
        )
    manifest_path = output_dir / MANIFEST_FILENAME
    temp_path = temp_path_for(manifest_path)
    try:
        temp_path.write_bytes(export_manifest_bytes(entries))
        temp_path.replace(manifest_path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_export_plan_to_zip(plan: ExportSessionsPlan) -> None:
    if plan.output_path is None:
        raise CliError("Zip export requires an output path.")
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = temp_path_for(plan.output_path)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest_entries = []
            for session_plan in plan.session_plans:
                data = export_plan_jsonl_bytes(session_plan)
                archive.writestr(
                    session_plan.output_path.as_posix(),
                    data,
                )
                manifest_entries.append(
                    export_manifest_entry(
                        session_plan,
                        session_plan.output_path.as_posix(),
                        file_fingerprint_from_bytes(data),
                    )
                )
            archive.writestr(MANIFEST_FILENAME, export_manifest_bytes(manifest_entries))
        temp_path.replace(plan.output_path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def export_sessions(
    *,
    targets: Sequence[str],
    codex_home: Path,
    output: Path | None = None,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    all_sessions: bool = False,
    only: Sequence[str] = (),
    exclude: Sequence[str] = (),
    started_after: str | None = None,
    started_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    force: bool = False,
) -> ExportSessionsResult:
    plan = plan_sessions_export(
        targets=targets,
        codex_home=codex_home,
        output=output,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        all_sessions=all_sessions,
        only=only,
        exclude=exclude,
        started_after=started_after,
        started_before=started_before,
        updated_after=updated_after,
        updated_before=updated_before,
        force=force,
    )
    return write_export_sessions_plan(plan)


def write_export_sessions_plan(plan: ExportSessionsPlan) -> ExportSessionsResult:
    if plan.output_kind == EXPORT_OUTPUT_ZIP:
        write_export_plan_to_zip(plan)
    else:
        for session_plan in plan.session_plans:
            write_export_plan_to_file(session_plan)
        if plan.output_kind == EXPORT_OUTPUT_DIRECTORY and plan.session_plans:
            write_export_manifest_to_directory(plan)
    return ExportSessionsResult(plan=plan)


def empty_import_sessions_plan(warnings: Sequence[str] = ()) -> ImportSessionsPlan:
    return ImportSessionsPlan(
        import_plans=(),
        fast_forward_plans=(),
        skipped=(),
        skipped_equivalent=(),
        skipped_local_ahead=(),
        duplicates=(),
        conflicts=(),
        diverged=(),
        failures=(),
        warnings=tuple(warnings),
    )


def import_plan_session_ids(plan: ImportSessionsPlan) -> set[str]:
    session_ids: set[str] = set()
    for import_plan in (*plan.import_plans, *plan.fast_forward_plans):
        session_ids.add(normalize_session_id(import_plan.session_id))
    for skipped in plan.skipped:
        session_ids.add(normalize_session_id(skipped.session_id))
    for skipped_history in (*plan.skipped_equivalent, *plan.skipped_local_ahead):
        session_ids.add(normalize_session_id(skipped_history.session_id))
    for duplicate in plan.duplicates:
        session_ids.add(normalize_session_id(duplicate.session_id))
    for conflict in plan.conflicts:
        session_ids.add(normalize_session_id(conflict.session_id))
    for diverged in plan.diverged:
        session_ids.add(normalize_session_id(diverged.session_id))
    return session_ids


def plan_missing_sessions_export_to_directory(
    *,
    codex_home: Path,
    output: Path,
    excluded_session_ids: set[str],
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ExportSessionsPlan:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    output_dir = output.expanduser()
    if not resolved_sessions_dir.exists():
        return ExportSessionsPlan(
            session_plans=(),
            output_kind=EXPORT_OUTPUT_DIRECTORY,
            output_path=output_dir,
            force=False,
            filtered_out_count=0,
        )

    index_path = session_index_path or codex_home / "session_index.jsonl"
    failures: list[ExportFailure] = []
    candidates = export_session_candidates(
        codex_home=codex_home,
        session_index_path=index_path,
        sessions_dir=resolved_sessions_dir,
        document_cache=document_cache,
        failures=failures,
    )
    selected_candidates = [
        candidate
        for candidate in candidates
        if normalize_session_id(candidate.session_id) not in excluded_session_ids
    ]
    plans: list[ExportSessionPlan] = []
    already_exported_count = 0
    for candidate in selected_candidates:
        candidate_output_path = export_output_path_for_candidate(
            candidate, output_dir, EXPORT_OUTPUT_DIRECTORY
        )
        candidate_plan = export_plan_for_candidate(
            candidate,
            candidate_output_path,
            force=False,
            check_output_file=False,
        )
        if candidate_output_path.exists():
            try:
                intended_fingerprint = file_fingerprint_from_bytes(
                    export_plan_jsonl_bytes(candidate_plan)
                )
                if intended_fingerprint == file_fingerprint(candidate_output_path):
                    already_exported_count += 1
                    continue
            except (OSError, ValueError) as exc:
                failures.append(ExportFailure(source_path=candidate.source_path, message=str(exc)))
                continue
            failures.append(
                ExportFailure(
                    source_path=candidate.source_path,
                    message=(
                        "Sync output already exists with different content: "
                        f"{candidate_output_path}"
                    ),
                )
            )
            continue
        plans.append(candidate_plan)
    warnings = tuple(warning for plan in plans for warning in plan.warnings)
    return ExportSessionsPlan(
        session_plans=tuple(plans),
        output_kind=EXPORT_OUTPUT_DIRECTORY,
        output_path=output_dir,
        force=False,
        filtered_out_count=len(candidates) - len(selected_candidates),
        already_present_count=already_exported_count,
        failures=tuple(failures),
        warnings=warnings,
    )


def plan_sync_import(
    sync_dir: Path,
    codex_home: Path,
    session_index_path: Path | None,
    sessions_dir: Path | None,
    *,
    persist_local_fingerprint_cache: bool,
    document_cache: dict[Path, SearchDocument] | None = None,
) -> ImportSessionsPlan:
    if not sync_dir.exists():
        return empty_import_sessions_plan()
    if not sync_dir.is_dir():
        raise CliError(f"Sync path is not a directory: {sync_dir}")
    try:
        return plan_sessions_import(
            source_path=sync_dir,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            merge=True,
            persist_local_fingerprint_cache=persist_local_fingerprint_cache,
            document_cache=document_cache,
        )
    except CliError as exc:
        if str(exc).startswith("No rollout JSONL files found in import directory:"):
            _, manifest_warnings = read_directory_manifest(sync_dir)
            return empty_import_sessions_plan(manifest_warnings)
        raise


def plan_sessions_sync(
    sync_dir: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    persist_local_fingerprint_cache: bool = True,
) -> SyncSessionsPlan:
    resolved_sync_dir = sync_dir.expanduser().resolve()
    document_cache: dict[Path, SearchDocument] = {}
    import_plan = plan_sync_import(
        resolved_sync_dir,
        codex_home,
        session_index_path,
        sessions_dir,
        persist_local_fingerprint_cache=persist_local_fingerprint_cache,
        document_cache=document_cache,
    )
    export_plan = plan_missing_sessions_export_to_directory(
        codex_home=codex_home,
        output=resolved_sync_dir,
        excluded_session_ids=import_plan_session_ids(import_plan),
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        document_cache=document_cache,
    )
    return SyncSessionsPlan(
        sync_dir=resolved_sync_dir,
        import_plan=import_plan,
        export_plan=export_plan,
    )


def sync_sessions(
    sync_dir: Path,
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    reset_state_cache: bool = True,
) -> SyncSessionsResult:
    plan = plan_sessions_sync(
        sync_dir=sync_dir,
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        persist_local_fingerprint_cache=False,
    )
    import_result = None
    if plan.import_plan.import_plans or plan.import_plan.fast_forward_plans:
        import_result = import_sessions(
            source_path=plan.sync_dir,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            merge=True,
            reset_state_cache=reset_state_cache,
        )
    else:
        import_result = ImportSessionsResult(
            plan=plan.import_plan,
            session_index_backup_path=None,
            rollout_backup_paths=(),
            state_cache_backups=(),
            state_cache_reset_error=None,
            state_cache_reset_skipped=False,
        )

    if plan.export_plan.session_plans:
        plan.sync_dir.mkdir(parents=True, exist_ok=True)
    export_result = write_export_sessions_plan(plan.export_plan)
    return SyncSessionsResult(
        plan=SyncSessionsPlan(
            sync_dir=plan.sync_dir,
            import_plan=import_result.plan,
            export_plan=export_result.plan,
        ),
        import_result=import_result,
        export_result=export_result,
    )
