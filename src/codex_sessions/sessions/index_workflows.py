from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_sessions.codex.state import (
    CodexStateError,
    backup_dir_for,
    backup_file,
    backup_label,
    backup_session_index,
    remove_backup_dir_if_empty,
    restore_file_backup,
    restore_session_index_backup,
)
from codex_sessions.errors import CliError
from codex_sessions.search.sessions import load_search_documents
from codex_sessions.sessions.display import (
    SessionDisplayInfo,
    format_session_display_info,
    indexed_session_display_info,
    unindexed_session_display_info,
)
from codex_sessions.sessions.documents import (
    infer_search_document_title,
    inferred_thread_name,
)
from codex_sessions.sessions.files import (
    ARCHIVES_EXCLUDE,
    ARCHIVES_ONLY,
    SessionFile,
    discover_session_files,
    file_modified_at,
    format_session_file_path,
    format_session_root_path,
    session_roots,
)
from codex_sessions.sessions.index import (
    append_session_index_records,
    normalize_session_id,
    read_session_index,
    resolve_session_index_record,
    session_index_record_id,
    session_index_record_thread_name,
    session_index_records,
    write_session_index_records,
)
from codex_sessions.sessions.paths import resolve_session_id
from codex_sessions.sessions.rollout import (
    read_rollout_records,
    renamed_rollout_records,
    write_rollout_records,
)


@dataclass(frozen=True)
class RepairIndexCandidate:
    session_id: str
    thread_name: str
    updated_at: datetime | None
    relative_path: str


@dataclass(frozen=True)
class RepairIndexResult:
    candidates: tuple[RepairIndexCandidate, ...]
    warnings: tuple[str, ...]
    skipped_without_id: int
    session_index_backup_path: Path | None


@dataclass(frozen=True)
class RenameSessionResult:
    session_id: str
    old_thread_name: str
    new_thread_name: str
    index_changed: bool
    rollout_changed: bool
    rollout_path: Path | None
    rollout_backup_path: Path | None
    rollout_thread_name: str | None
    changed: bool
    session_index_backup_path: Path | None


def list_session_lines(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    archives: str = ARCHIVES_EXCLUDE,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> list[str]:
    infos, _ = list_session_display_infos_with_warnings(
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        archives=archives,
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )
    return [format_session_display_info(info) for info in infos]


def list_session_lines_with_warnings(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    archives: str = ARCHIVES_EXCLUDE,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> tuple[list[str], list[str]]:
    infos, warnings = list_session_display_infos_with_warnings(
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        archives=archives,
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )
    return [format_session_display_info(info) for info in infos], warnings


def list_session_display_infos_with_warnings(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    archives: str = ARCHIVES_EXCLUDE,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> tuple[list[SessionDisplayInfo], list[str]]:
    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_entries = read_session_index(index_path)
    warnings: list[str] = []
    session_files_with_titles: list[tuple[SessionFile, str | None]] = []
    for root in session_roots(codex_home, sessions_dir, archives=archives):
        if not root.path.exists() and not root.required:
            continue
        documents, root_warnings = load_search_documents(
            codex_home=codex_home,
            sessions_dir=root.path,
            redaction="...",
            warning_path_prefix="archived_sessions" if root.archived else None,
            use_cache=use_cache,
            rebuild_cache=rebuild_cache,
        )
        warnings.extend(root_warnings)
        session_files_with_titles.extend(
            (
                SessionFile(
                    path=session_path,
                    relative_path=format_session_root_path(session_path, root),
                    session_id=document.session_id,
                    started_at=document.started_at,
                    ended_at=document.last_activity_at,
                    session_id_is_canonical=document.session_id_is_canonical,
                    identity_warning=document.identity_warning,
                    identity_status=document.identity_status,
                    modified_at=file_modified_at(session_path),
                    archived=root.archived,
                ),
                infer_search_document_title(document),
            )
            for session_path, document in documents
        )
    session_files_by_id: dict[str, list[SessionFile]] = {}
    for session_file, _inferred_title in session_files_with_titles:
        if session_file.session_id:
            normalized_id = normalize_session_id(session_file.session_id)
            session_files_by_id.setdefault(normalized_id, []).append(session_file)
    for matching_files in session_files_by_id.values():
        if len(matching_files) > 1:
            rendered_matches = ", ".join(
                session_file.relative_path for session_file in matching_files
            )
            warnings.append(
                f"Multiple rollout files have session ID {matching_files[0].session_id}: "
                f"{rendered_matches}"
            )

    # Build index rows before rollout-only rows so equal timestamps retain stable cross-check order.
    indexed_ids = {normalize_session_id(entry.session_id) for entry in index_entries}
    excluded_archived_ids: set[str] = set()
    if archives == ARCHIVES_EXCLUDE and sessions_dir is None:
        # TODO: Replace this scan when list and transfer share an archive-aware cache.
        excluded_archived_ids = {
            normalize_session_id(session_file.session_id)
            for session_file in discover_session_files(
                codex_home / "archived_sessions", archived=True
            )
            if session_file.session_id
        }
    infos = []
    for entry in index_entries:
        normalized_id = normalize_session_id(entry.session_id)
        matching_files = session_files_by_id.get(normalized_id, [])
        if not matching_files:
            if archives == ARCHIVES_ONLY or normalized_id in excluded_archived_ids:
                continue
            infos.append(indexed_session_display_info(entry, None))
            continue
        infos.extend(
            indexed_session_display_info(entry, session_file) for session_file in matching_files
        )

    for session_file, inferred_title in session_files_with_titles:
        if session_file.session_id and normalize_session_id(session_file.session_id) in indexed_ids:
            continue
        infos.append(unindexed_session_display_info(session_file, inferred_title))

    earliest = datetime.min.replace(tzinfo=timezone.utc)
    infos.sort(key=lambda info: (info.ended_at is not None, info.ended_at or earliest))
    return infos, warnings


def missing_session_index_candidates(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> tuple[list[RepairIndexCandidate], list[str], int]:
    index_path = session_index_path or codex_home / "session_index.jsonl"
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    if not resolved_sessions_dir.exists():
        raise CliError(f"Sessions directory not found: {resolved_sessions_dir}")

    index_entries = read_session_index(index_path)
    indexed_ids = {normalize_session_id(entry.session_id) for entry in index_entries}
    documents, warnings = load_search_documents(
        codex_home=codex_home,
        sessions_dir=resolved_sessions_dir,
        redaction="...",
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )

    candidates = []
    skipped_without_id = 0
    for session_path, document in documents:
        if not document.session_id or not document.session_id_is_canonical:
            skipped_without_id += 1
            continue
        if normalize_session_id(document.session_id) in indexed_ids:
            continue
        candidates.append(
            RepairIndexCandidate(
                session_id=document.session_id,
                thread_name=inferred_thread_name(document),
                updated_at=document.ended_at or document.started_at,
                relative_path=format_session_file_path(session_path, resolved_sessions_dir),
            )
        )

    return candidates, warnings, skipped_without_id


def optional_session_file_for_id(session_id: str, codex_home: Path) -> Path | None:
    try:
        return resolve_session_id(session_id, codex_home, require_canonical=True)
    except CliError as exc:
        if str(exc).startswith("No Codex session found for ID:"):
            return None
        raise


def rename_session_index_entry(
    codex_home: Path,
    session_index_path: Path | None,
    target: str,
    new_thread_name: str,
) -> RenameSessionResult:
    normalized_new_thread_name = new_thread_name.strip()
    if not normalized_new_thread_name:
        raise CliError("New session title must not be empty.")

    index_path = session_index_path or codex_home / "session_index.jsonl"
    records = session_index_records(index_path)
    record_index, record = resolve_session_index_record(records, target)
    session_id = session_index_record_id(record)
    if session_id is None:
        raise CliError(f"Matched session_index.jsonl entry has no session id: {target}")

    old_thread_name = session_index_record_thread_name(record)
    index_changed = old_thread_name != normalized_new_thread_name
    rollout_path = optional_session_file_for_id(session_id, codex_home)
    updated_rollout_records: list[dict[str, Any]] | None = None
    rollout_thread_name: str | None = None
    rollout_changed = False
    if rollout_path is not None:
        rollout_records = read_rollout_records(rollout_path)
        # The Codex UI can restore titles from rollout events, so rename both index and rollout.
        updated_rollout_records, rollout_thread_name, rollout_changed = renamed_rollout_records(
            rollout_records, session_id, normalized_new_thread_name
        )

    if not index_changed and not rollout_changed:
        return RenameSessionResult(
            session_id=session_id,
            old_thread_name=old_thread_name,
            new_thread_name=normalized_new_thread_name,
            index_changed=False,
            rollout_changed=False,
            rollout_path=rollout_path,
            rollout_backup_path=None,
            rollout_thread_name=rollout_thread_name,
            changed=False,
            session_index_backup_path=None,
        )

    updated_records = list(records)
    if index_changed:
        updated_record = dict(record)
        updated_record["thread_name"] = normalized_new_thread_name
        updated_records[record_index] = updated_record

    label = backup_label()
    backup_dir = backup_dir_for(codex_home, label)
    index_backup_path = backup_session_index(index_path, backup_dir) if index_changed else None
    rollout_backup_path = (
        backup_file(rollout_path, backup_dir)
        if rollout_changed and rollout_path is not None
        else None
    )
    try:
        if index_changed:
            write_session_index_records(index_path, updated_records)
        if rollout_changed:
            if rollout_path is None or updated_rollout_records is None:
                raise CliError(f"No Codex rollout file found for ID: {session_id}")
            write_rollout_records(rollout_path, updated_rollout_records)
    except (CliError, CodexStateError, OSError) as exc:
        try:
            if rollout_path is not None and rollout_changed:
                restore_file_backup(rollout_path, rollout_backup_path)
            if index_changed:
                restore_session_index_backup(index_path, index_backup_path)
            remove_backup_dir_if_empty(backup_dir)
        except OSError as restore_exc:
            raise CliError(
                f"{exc} Also failed to restore Codex session files from backup: {restore_exc}"
            ) from restore_exc
        raise CliError(
            f"{exc} Rolled back Codex session files. Close all Codex sessions and retry."
        ) from exc

    return RenameSessionResult(
        session_id=session_id,
        old_thread_name=old_thread_name,
        new_thread_name=normalized_new_thread_name,
        index_changed=index_changed,
        rollout_changed=rollout_changed,
        rollout_path=rollout_path,
        rollout_backup_path=rollout_backup_path,
        rollout_thread_name=rollout_thread_name,
        changed=True,
        session_index_backup_path=index_backup_path,
    )


def repair_session_index(
    codex_home: Path,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> RepairIndexResult:
    index_path = session_index_path or codex_home / "session_index.jsonl"
    candidates, warnings, skipped_without_id = missing_session_index_candidates(
        codex_home=codex_home,
        session_index_path=index_path,
        sessions_dir=sessions_dir,
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )
    if not candidates:
        return RepairIndexResult(
            candidates=(),
            warnings=tuple(warnings),
            skipped_without_id=skipped_without_id,
            session_index_backup_path=None,
        )

    label = backup_label()
    backup_dir = backup_dir_for(codex_home, label)
    index_backup_path = backup_session_index(index_path, backup_dir)
    try:
        append_session_index_records(index_path, candidates)
    except (CliError, CodexStateError, OSError) as exc:
        try:
            restore_session_index_backup(index_path, index_backup_path)
            remove_backup_dir_if_empty(backup_dir)
        except OSError as restore_exc:
            raise CliError(
                f"{exc} Also failed to restore session_index.jsonl from backup: {restore_exc}"
            ) from restore_exc
        raise CliError(
            f"{exc} Rolled back session_index.jsonl. Close all Codex sessions and retry."
        ) from exc

    return RepairIndexResult(
        candidates=tuple(candidates),
        warnings=tuple(warnings),
        skipped_without_id=skipped_without_id,
        session_index_backup_path=index_backup_path,
    )
