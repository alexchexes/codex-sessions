import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codex_sessions.codex.state import (
    CodexStateError,
    backup_dir_for,
    backup_label,
    backup_session_index,
    remove_backup_dir_if_empty,
    reset_codex_state_cache,
    restore_file_backup,
    restore_session_index_backup,
)
from codex_sessions.errors import CliError
from codex_sessions.search.sessions import build_search_document
from codex_sessions.sessions.documents import SearchDocument, inferred_thread_name
from codex_sessions.sessions.files import (
    SessionFile,
    discover_session_files,
    session_id_from_path,
)
from codex_sessions.sessions.index import (
    format_session_index_timestamp,
    is_session_id,
    normalize_session_id,
    resolve_session_index_record,
    session_index_record_id,
    session_index_record_thread_name,
    session_index_records,
    write_session_index_records,
)
from codex_sessions.sessions.rollout import (
    ExportSessionPlan,
    ExportSessionResult,
    ImportSessionPlan,
    ImportSessionResult,
    export_title_slug,
    file_fingerprint,
    format_fingerprint,
    read_rollout_records,
    renamed_rollout_records,
    resolve_export_output_path,
    rollout_filename_date,
    write_rollout_records,
)


def import_target_date(source_path: Path, document: SearchDocument) -> tuple[str, str, str]:
    filename_date = rollout_filename_date(source_path)
    if filename_date is not None:
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
    if source_path.name.startswith("rollout-") and session_id_from_path(source_path):
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


def existing_session_files_for_id(session_id: str, sessions_dir: Path) -> list[SessionFile]:
    normalized_id = normalize_session_id(session_id)
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
) -> ImportSessionPlan:
    expanded_source_path = source_path.expanduser().resolve()
    if not expanded_source_path.exists():
        raise CliError(f"Input file not found: {source_path}")
    if not expanded_source_path.is_file():
        raise CliError(f"Input path is not a file: {source_path}")

    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    document = build_search_document(expanded_source_path, "...")
    if document.session_id is None:
        raise CliError(f"Cannot infer session id from rollout: {source_path}")

    target_path = import_target_path(expanded_source_path, resolved_sessions_dir, document)
    source_fingerprint = file_fingerprint(expanded_source_path)
    existing_files = existing_session_files_for_id(document.session_id, resolved_sessions_dir)
    existing_rollout_path = first_existing_rollout_for_import(existing_files, target_path)
    existing_rollout_fingerprint = (
        file_fingerprint(existing_rollout_path) if existing_rollout_path is not None else None
    )

    if existing_rollout_path is not None:
        if source_fingerprint == existing_rollout_fingerprint:
            raise CliError(
                "Session already imported with identical rollout file: "
                f"{existing_rollout_path} ({format_fingerprint(source_fingerprint)})"
            )
        existing_fingerprint = (
            format_fingerprint(existing_rollout_fingerprint)
            if existing_rollout_fingerprint
            else "UNKNOWN"
        )
        raise CliError(
            "Session already imported, but rollout file differs. "
            f"Existing: {existing_rollout_path} "
            f"({existing_fingerprint}); "
            f"import: {expanded_source_path} ({format_fingerprint(source_fingerprint)})."
        )

    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_records = session_index_records(index_path) if index_path.exists() else []
    existing_index_match = existing_index_record_for_id(index_records, document.session_id)
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
        expanded_source_path, document.session_id, normalized_name
    )

    return ImportSessionPlan(
        source_path=expanded_source_path,
        target_path=target_path,
        session_index_path=index_path,
        session_id=document.session_id,
        thread_name=normalized_name,
        started_at=document.started_at,
        ended_at=document.ended_at,
        index_action=index_action,
        existing_index_thread_name=existing_index_thread_name,
        source_fingerprint=source_fingerprint,
        rollout_will_be_rewritten=rollout_will_be_rewritten,
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
    existing_index_match = existing_index_record_for_id(records, plan.session_id)
    if plan.index_action == "add":
        if existing_index_match is not None:
            return records
        return [*records, session_index_record_for_import_plan(plan)]
    if plan.index_action == "update":
        if existing_index_match is None:
            raise CliError(f"No session_index.jsonl entry found for ID: {plan.session_id}")
        record_index, record = existing_index_match
        updated_records = list(records)
        updated_record = dict(record)
        updated_record["thread_name"] = plan.thread_name
        updated_records[record_index] = updated_record
        return updated_records
    return records


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
) -> ImportSessionResult:
    plan = plan_bare_rollout_import(
        source_path=source_path,
        codex_home=codex_home,
        session_index_path=session_index_path,
        sessions_dir=sessions_dir,
        name=name,
    )
    index_changed = plan.index_action in {"add", "update"}
    updated_index_records = session_index_records_for_import(plan) if index_changed else None

    label = backup_label()
    backup_dir = backup_dir_for(codex_home, label)
    index_backup_path = (
        backup_session_index(plan.session_index_path, backup_dir) if index_changed else None
    )
    rollout_written = False
    try:
        if index_changed:
            if updated_index_records is None:
                raise CliError("Could not prepare session_index.jsonl update.")
            plan.session_index_path.parent.mkdir(parents=True, exist_ok=True)
            write_session_index_records(plan.session_index_path, updated_index_records)
        copy_or_rewrite_import_rollout(plan)
        rollout_written = True
        state_cache_backups = reset_codex_state_cache(codex_home, backup_dir)
    except (CliError, CodexStateError, OSError) as exc:
        try:
            if rollout_written or plan.target_path.exists():
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

    return ImportSessionResult(
        plan=plan,
        session_index_backup_path=index_backup_path,
        state_cache_backups=state_cache_backups,
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
    document = build_search_document(source_path, "...")
    resolved_session_id = document.session_id or session_file.session_id or session_id
    index_thread_name = (
        session_index_record_thread_name(index_record) if index_record is not None else ""
    )
    thread_name = index_thread_name or inferred_thread_name(document)
    if not thread_name:
        raise CliError("Exported session title must not be empty.")

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
