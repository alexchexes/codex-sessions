import argparse
import errno
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rich.text import Text

from codex_sessions.cli_args import (
    cli_prog_from_argv0,
    parse_args,
    parse_export_args,
    parse_import_args,
    parse_list_args,
    parse_markdown_include,
    parse_rename_args,
    parse_repair_index_args,
    parse_reset_state_cache_args,
    parse_search_args,
    resolve_markdown_tool_mode,
)
from codex_sessions.codex.state import (
    CodexStateError,
    StateCacheBackup,
    reset_codex_state_cache_with_backup,
)
from codex_sessions.core.terminal import encode_for_output, terminal_console
from codex_sessions.errors import CliError
from codex_sessions.formats.markdown.output import MarkdownOptions, convert_jsonl_to_markdown
from codex_sessions.formats.yaml import convert_jsonl_to_yaml_stream
from codex_sessions.search.core import SearchOptions
from codex_sessions.search.output import render_search_results
from codex_sessions.search.sessions import (
    search_sessions,
)
from codex_sessions.sessions.display import (
    NO_SESSION_INDEX_ENTRY,
    SessionDisplayInfo,
    styled_session_display_text,
)
from codex_sessions.sessions.index_workflows import (
    RepairIndexCandidate,
    list_session_display_infos_with_warnings,
    missing_session_index_candidates,
    rename_session_index_entry,
    repair_session_index,
)
from codex_sessions.sessions.paths import (
    infer_output_format,
    resolve_conversion_input,
    resolve_output_path,
)
from codex_sessions.sessions.rollout import (
    ExportSessionPlan,
    ExportSessionsPlan,
    ImportConflict,
    ImportDivergedConflict,
    ImportDuplicateSession,
    ImportFailure,
    ImportSessionPlan,
    ImportSessionsPlan,
    ImportSessionsResult,
    ImportSkippedHistory,
    ImportSkippedSession,
    format_fingerprint,
)
from codex_sessions.sessions.transfer import (
    EXPORT_OUTPUT_DIRECTORY,
    EXPORT_OUTPUT_ZIP,
    export_sessions,
    import_sessions,
    plan_sessions_export,
    plan_sessions_import,
)

__version__ = "0.1.0"

STYLE_ATTENTION = "bold bright_yellow"
STYLE_ERROR = "bold bright_red"
STYLE_HEADING = "bold bright_blue"
STYLE_LABEL = "bright_blue"
STYLE_SECONDARY = "bright_black"
STYLE_SUCCESS = "bright_green"
STYLE_SUCCESS_STRONG = "bold bright_green"


def repair_index_candidate_text(candidate: RepairIndexCandidate, *, indent: str = "") -> Text:
    updated_at = candidate.updated_at.isoformat() if candidate.updated_at else "UNKNOWN"
    rendered = Text()
    append_cli_text(rendered, indent, sys.stdout.encoding)
    append_cli_text(rendered, candidate.session_id, sys.stdout.encoding, style=STYLE_SECONDARY)
    append_cli_text(rendered, " - ", sys.stdout.encoding, style=STYLE_SECONDARY)
    append_cli_text(rendered, candidate.thread_name, sys.stdout.encoding)
    append_cli_text(rendered, " - ", sys.stdout.encoding, style=STYLE_SECONDARY)
    append_cli_text(rendered, candidate.relative_path, sys.stdout.encoding, style=STYLE_SECONDARY)
    append_cli_text(rendered, " - ", sys.stdout.encoding, style=STYLE_SECONDARY)
    append_cli_text(rendered, f"updated_at: {updated_at}", sys.stdout.encoding, style="bright_cyan")
    return rendered


def count_text(prefix: str, count: int, *, style: str = "") -> Text:
    rendered = Text()
    append_cli_text(rendered, prefix, sys.stdout.encoding, style=style)
    append_cli_text(rendered, count, sys.stdout.encoding, style=f"bold {style}".strip())
    return rendered


def run_list_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    sessions_dir = (
        args.sessions_dir.expanduser().resolve() if args.sessions_dir else codex_home / "sessions"
    )

    try:
        session_infos, warnings = list_session_display_infos_with_warnings(
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            use_cache=not args.no_cache,
            rebuild_cache=args.rebuild_cache,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    for warning in warnings:
        print_cli_line(
            f"Warning: {warning}",
            style=STYLE_ATTENTION,
            stream=sys.stderr,
        )
    for info in session_infos:
        print_cli_line(styled_session_display_text(info, sys.stdout.encoding))
    if any(info.status == NO_SESSION_INDEX_ENTRY for info in session_infos):
        print_cli_line()
        print_cli_line(
            "Run 'codex-sessions repair-index' to add missing session_index.jsonl entries.",
            style=STYLE_ATTENTION,
        )
    return 0


def run_search_command(args: argparse.Namespace) -> int:
    if args.line_width < 20:
        raise SystemExit("--line-width must be at least 20")
    if args.max_lines_per_session < 0:
        raise SystemExit("--max-lines-per-session must be zero or greater")

    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    sessions_dir = (
        args.sessions_dir.expanduser().resolve() if args.sessions_dir else codex_home / "sessions"
    )
    options = SearchOptions(
        pattern=args.pattern,
        regex=args.regex,
        ignore_case=args.ignore_case,
        line_width=args.line_width,
        max_lines_per_session=args.max_lines_per_session,
        include_metadata=args.metadata or args.all,
        include_tools=args.tools or args.all,
        color=args.color,
        redaction=args.redact_encrypted,
    )

    try:
        results, warnings = search_sessions(
            codex_home=codex_home,
            options=options,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            use_cache=not args.no_cache,
            rebuild_cache=args.rebuild_cache,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    try:
        render_search_results(results, warnings, args.color)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.EPIPE}:
            raise
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    return 0 if results else 1


def print_cli_line(
    line: object = "",
    *,
    style: str | None = None,
    stream: TextIO | None = None,
) -> None:
    output_stream = stream or sys.stdout
    text = (
        line.copy()
        if isinstance(line, Text)
        else Text(
            encode_for_output(str(line), output_stream.encoding),
            style=style or "",
        )
    )
    if isinstance(line, Text) and style:
        text.stylize(style)
    terminal_console(output_stream).print(text, soft_wrap=True)


CliLine = str | Text


def print_cli_lines(lines: Sequence[CliLine], *, style: str | None = None) -> None:
    for line in lines:
        print_cli_line(line, style=style)


def append_cli_text(text: Text, value: object, encoding: str | None, *, style: str = "") -> None:
    text.append(encode_for_output(str(value), encoding), style=style)


def cli_text(value: object, *, style: str = "") -> Text:
    return Text(encode_for_output(str(value), sys.stdout.encoding), style=style)


def session_display_info(
    session_id: str,
    thread_name: str,
    started_at: datetime | None,
    ended_at: datetime | None,
) -> SessionDisplayInfo:
    return SessionDisplayInfo(
        session_id=session_id,
        title=thread_name,
        started_at=started_at,
        ended_at=ended_at,
    )


def prefixed_session_info_text(
    prefix: str,
    info: SessionDisplayInfo,
    *,
    prefix_style: str,
) -> Text:
    rendered = Text()
    append_cli_text(rendered, prefix, sys.stdout.encoding, style=prefix_style)
    rendered.append_text(styled_session_display_text(info, sys.stdout.encoding))
    return rendered


def path_arrow_text(path: object, *, indent: str = "  ") -> Text:
    return cli_text(f"{indent}-> {path}", style=STYLE_SECONDARY)


def labeled_text_lines(
    items: Sequence[tuple[str, object, str | None]],
    *,
    indent: str = "",
) -> list[Text]:
    if not items:
        return []
    label_width = max(len(label) + 1 for label, _, _ in items)
    lines = []
    for label, value, value_style in items:
        rendered = Text()
        append_cli_text(rendered, indent, sys.stdout.encoding)
        append_cli_text(
            rendered,
            f"{label + ':':<{label_width}}",
            sys.stdout.encoding,
            style=STYLE_LABEL,
        )
        append_cli_text(rendered, " ", sys.stdout.encoding)
        append_cli_text(rendered, value, sys.stdout.encoding, style=value_style or "")
        lines.append(rendered)
    return lines


def print_labeled_text_lines(
    items: Sequence[tuple[str, object, str | None]],
    *,
    indent: str = "",
) -> None:
    for line in labeled_text_lines(items, indent=indent):
        print_cli_line(line)


def print_write_result(count: int, document_label: str, output_path: Path) -> None:
    rendered = Text()
    append_cli_text(rendered, "Wrote ", sys.stdout.encoding, style=STYLE_SUCCESS)
    append_cli_text(rendered, count, sys.stdout.encoding, style=STYLE_SUCCESS_STRONG)
    append_cli_text(rendered, f" {document_label} to ", sys.stdout.encoding, style=STYLE_SUCCESS)
    append_cli_text(rendered, output_path, sys.stdout.encoding, style=STYLE_SECONDARY)
    print_cli_line(rendered)


def labeled_lines(items: Sequence[tuple[str, object]], *, indent: str = "") -> list[Text]:
    return labeled_text_lines([(label, value, None) for label, value in items], indent=indent)


def path_block_lines(label: str, path: object, *, indent: str = "") -> list[Text]:
    return labeled_text_lines([(label, path, STYLE_SECONDARY)], indent=indent)


def indented_text_lines(text: str, *, indent: str = "  ") -> list[str]:
    return [f"{indent}{line}" if line else indent.rstrip() for line in text.splitlines()]


def print_encoded_lines(lines: Sequence[CliLine]) -> None:
    print_cli_lines(lines)


def print_path_backup_block(
    session_index_backup_path: Path | None,
    rollout_backup_paths: Sequence[Path] = (),
) -> None:
    if session_index_backup_path is None and not rollout_backup_paths:
        return
    print_cli_line("Backups:", style=STYLE_HEADING)
    if session_index_backup_path is not None:
        print_labeled_text_lines(
            [("Index", session_index_backup_path, STYLE_SECONDARY)],
            indent="  ",
        )
    if rollout_backup_paths:
        if len(rollout_backup_paths) == 1:
            print_labeled_text_lines(
                [("Rollout", rollout_backup_paths[0], STYLE_SECONDARY)],
                indent="  ",
            )
        else:
            print_cli_line("  Rollouts:", style=STYLE_LABEL)
            for path in rollout_backup_paths:
                print_cli_line(f"    {path}", style=STYLE_SECONDARY)


def print_state_cache_backups(backups: tuple[StateCacheBackup, ...]) -> None:
    print_cli_line("State cache reset OK.", style=STYLE_SUCCESS)
    if not backups:
        print_cli_line("No Codex state cache files found.", style=STYLE_SECONDARY)
        return
    print_cli_line("  Backups:", style=STYLE_HEADING)
    for backup in backups:
        print_cli_line(f"  - {backup.original_path}", style=STYLE_SECONDARY)
        print_cli_line(f"    -> {backup.backup_path}", style=STYLE_SECONDARY)


def print_deferred_state_cache_command() -> None:
    print_cli_line(
        "State cache reset skipped. To reset, run this with all Codex sessions closed:",
        style=STYLE_ATTENTION,
    )
    print_cli_line("  codex-sessions reset-state-cache")


def can_retry_state_cache_reset_interactively(non_interactive: bool) -> bool:
    return not non_interactive and sys.stdin.isatty() and sys.stdout.isatty()


def retry_state_cache_reset_interactively(codex_home: Path) -> None:
    while True:
        print_cli_line()
        print_cli_line(
            "Close all Codex sessions, then press Enter to retry state cache reset.",
            style=STYLE_ATTENTION,
        )
        try:
            input("Press Ctrl+C to keep these changes and reset later. ")
        except (EOFError, KeyboardInterrupt):
            print_cli_line()
            print_deferred_state_cache_command()
            return
        print_cli_line()
        print_cli_line("Retrying state cache reset...", style=STYLE_LABEL)
        print_cli_line()
        try:
            backups = reset_codex_state_cache_with_backup(codex_home)
        except (CodexStateError, OSError) as exc:
            print_cli_line("State cache reset still blocked:", style=STYLE_ATTENTION)
            print_cli_lines(indented_text_lines(str(exc)), style=STYLE_SECONDARY)
            continue
        print_state_cache_backups(backups)
        return


def print_mutation_state_cache_status(
    codex_home: Path,
    backups: tuple[StateCacheBackup, ...],
    reset_error: str | None,
    reset_skipped: bool,
    *,
    non_interactive: bool,
) -> None:
    print_cli_line()
    if reset_skipped:
        print_deferred_state_cache_command()
        return
    if reset_error is not None:
        print_cli_line("State cache reset deferred:", style=STYLE_ATTENTION)
        print_cli_lines(indented_text_lines(reset_error), style=STYLE_SECONDARY)
        if can_retry_state_cache_reset_interactively(non_interactive):
            retry_state_cache_reset_interactively(codex_home)
        else:
            print_cli_line(
                "To reset later, run this with all Codex sessions closed:",
                style=STYLE_ATTENTION,
            )
            print_cli_line("  codex-sessions reset-state-cache")
        return
    print_state_cache_backups(backups)


def run_reset_state_cache_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    try:
        backups = reset_codex_state_cache_with_backup(codex_home)
    except (CodexStateError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    print_state_cache_backups(backups)
    return 0


def run_repair_index_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    sessions_dir = (
        args.sessions_dir.expanduser().resolve() if args.sessions_dir else codex_home / "sessions"
    )

    if args.dry_run:
        try:
            candidates, warnings, skipped_without_id = missing_session_index_candidates(
                codex_home=codex_home,
                session_index_path=session_index_path,
                sessions_dir=sessions_dir,
                use_cache=not args.no_cache,
                rebuild_cache=args.rebuild_cache,
            )
        except (CliError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        for warning in warnings:
            print_cli_line(f"Warning: {warning}", style=STYLE_ATTENTION, stream=sys.stderr)
        print_cli_line(
            count_text(
                "Missing session_index.jsonl entries: ",
                len(candidates),
                style=STYLE_ATTENTION,
            )
        )
        if candidates:
            print_cli_line("Would add:", style=STYLE_HEADING)
            for candidate in candidates:
                print_cli_line(repair_index_candidate_text(candidate, indent="  "))
            print_cli_line("State cache reset required after repair.", style=STYLE_ATTENTION)
        else:
            print_cli_line("No missing session_index.jsonl entries found.")
        if skipped_without_id:
            print_cli_line(
                f"Skipped rollout files without session id: {skipped_without_id}",
                style=STYLE_ATTENTION,
            )
        return 0

    try:
        result = repair_session_index(
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            use_cache=not args.no_cache,
            rebuild_cache=args.rebuild_cache,
            reset_state_cache=not args.no_reset_state_cache,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    for warning in result.warnings:
        print_cli_line(f"Warning: {warning}", style=STYLE_ATTENTION, stream=sys.stderr)
    print_cli_line(
        count_text(
            "Added session_index.jsonl entries: ",
            len(result.candidates),
            style=STYLE_SUCCESS,
        )
    )
    if result.candidates:
        for candidate in result.candidates:
            print_cli_line(repair_index_candidate_text(candidate))
        if result.session_index_backup_path is not None:
            print_cli_line()
            print_path_backup_block(result.session_index_backup_path)
        print_mutation_state_cache_status(
            codex_home,
            result.state_cache_backups,
            result.state_cache_reset_error,
            result.state_cache_reset_skipped,
            non_interactive=args.non_interactive,
        )
    else:
        print_cli_line("No missing session_index.jsonl entries found.")
    if result.skipped_without_id:
        print_cli_line(
            f"Skipped rollout files without session id: {result.skipped_without_id}",
            style=STYLE_ATTENTION,
        )
    return 0


def run_rename_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    new_thread_name = " ".join(args.name).strip()

    try:
        result = rename_session_index_entry(
            codex_home=codex_home,
            session_index_path=session_index_path,
            target=args.target,
            new_thread_name=new_thread_name,
            reset_state_cache=not args.no_reset_state_cache,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    if not result.changed:
        print_cli_line("Session title already set:")
        print_cli_line(
            styled_session_display_text(
                session_display_info(
                    result.session_id,
                    result.new_thread_name,
                    None,
                    None,
                ),
                sys.stdout.encoding,
            )
        )
        return 0

    renamed = Text()
    append_cli_text(renamed, "Renamed session ", sys.stdout.encoding, style=STYLE_SUCCESS)
    append_cli_text(renamed, result.session_id, sys.stdout.encoding, style=STYLE_SECONDARY)
    print_cli_line(renamed)

    rename_lines: list[tuple[str, object, str | None]] = []
    if result.index_changed:
        rename_lines.append(("From", result.old_thread_name, None))
    if (
        result.rollout_changed
        and result.rollout_thread_name is not None
        and result.rollout_thread_name != result.old_thread_name
    ):
        rename_lines.append(("From (rollout)", result.rollout_thread_name, None))
    # TODO: surface inserted rollout title events in verbose mode (when that mode is added).
    rename_lines.append(("To", result.new_thread_name, None))
    if result.rollout_changed and result.rollout_path is not None:
        rename_lines.append(("Rollout file", result.rollout_path, STYLE_SECONDARY))
    if result.rollout_backup_path is not None:
        rename_lines.append(("Rollout backup", result.rollout_backup_path, STYLE_SECONDARY))
    if result.session_index_backup_path is not None:
        rename_lines.append(("Index backup", result.session_index_backup_path, STYLE_SECONDARY))
    print_labeled_text_lines(rename_lines, indent="  ")
    print_mutation_state_cache_status(
        codex_home,
        result.state_cache_backups,
        result.state_cache_reset_error,
        result.state_cache_reset_skipped,
        non_interactive=args.non_interactive,
    )
    return 0


def import_index_action_label(action: str) -> str:
    if action == "add":
        return "add session_index.jsonl entry"
    if action == "update":
        return "update session_index.jsonl title"
    if action == "advance":
        return "update session_index.jsonl title and timestamp"
    if action == "keep":
        return "keep existing session_index.jsonl entry"
    return action


def import_rollout_action_label(plan: ImportSessionPlan) -> str:
    action = "replace" if plan.replaces_existing_rollout else "copy"
    if plan.rollout_will_be_rewritten:
        return f"{action} with rollout title event update"
    return f"{action} unchanged"


def format_import_plan_lines(plan: ImportSessionPlan) -> list[CliLine]:
    lines: list[CliLine] = [
        styled_session_display_text(
            session_display_info(plan.session_id, plan.thread_name, plan.started_at, plan.ended_at),
            sys.stdout.encoding,
        ),
    ]
    if plan.existing_index_thread_name and plan.existing_index_thread_name != plan.thread_name:
        lines[1:1] = labeled_lines(
            [("Existing session_index.jsonl title", plan.existing_index_thread_name)]
        )
    lines.extend(
        [
            *path_block_lines("Source", plan.source_path, indent="  "),
            *path_block_lines("Target", plan.target_path, indent="  "),
            *labeled_lines(
                [
                    ("Fingerprint", format_fingerprint(plan.source_fingerprint)),
                    ("Action", import_rollout_action_label(plan)),
                ],
                indent="  ",
            ),
            *labeled_lines(
                [("Index action", import_index_action_label(plan.index_action))],
                indent="  ",
            ),
            "",
            cli_text("State cache reset required after import.", style=STYLE_ATTENTION),
        ]
    )
    return lines


def format_import_skipped_lines(skipped: ImportSkippedSession) -> list[CliLine]:
    return [
        cli_text(f"SKIPPED (identical) {skipped.session_id}", style=STYLE_LABEL),
        *path_block_lines("Existing", skipped.existing_path, indent="  "),
        *path_block_lines("Import", skipped.source_path, indent="  "),
        *labeled_lines([("Fingerprint", format_fingerprint(skipped.fingerprint))], indent="  "),
    ]


def format_import_history_skip_lines(skipped: ImportSkippedHistory, reason: str) -> list[CliLine]:
    return [
        cli_text(f"SKIPPED ({reason}) {skipped.session_id}", style=STYLE_LABEL),
        *path_block_lines("Existing", skipped.existing_path, indent="  "),
        *path_block_lines("Import", skipped.source_path, indent="  "),
        *labeled_lines(
            [
                ("Common records", skipped.common_comparable_records),
                ("Existing tail", skipped.existing_tail_comparable_records),
                ("Import tail", skipped.incoming_tail_comparable_records),
            ],
            indent="  ",
        ),
    ]


def format_import_conflict_lines(conflict: ImportConflict) -> list[CliLine]:
    existing_fingerprint = (
        format_fingerprint(conflict.existing_fingerprint)
        if conflict.existing_fingerprint
        else "UNKNOWN"
    )
    return [
        cli_text(f"ID conflict {conflict.session_id}", style=STYLE_ATTENTION),
        *path_block_lines("Local", conflict.existing_path, indent="  "),
        *labeled_lines([("Existing fingerprint", existing_fingerprint)], indent="  "),
        *path_block_lines("Import", conflict.source_path, indent="  "),
        *labeled_lines(
            [("Import fingerprint", format_fingerprint(conflict.source_fingerprint))],
            indent="  ",
        ),
    ]


def format_import_diverged_lines(conflict: ImportDivergedConflict) -> list[CliLine]:
    return [
        cli_text(f"Diverged {conflict.session_id}", style=STYLE_ATTENTION),
        *path_block_lines("Local", conflict.existing_path, indent="  "),
        *labeled_lines(
            [("Existing fingerprint", format_fingerprint(conflict.existing_fingerprint))],
            indent="  ",
        ),
        *path_block_lines("Import", conflict.source_path, indent="  "),
        *labeled_lines(
            [
                ("Import fingerprint", format_fingerprint(conflict.source_fingerprint)),
                ("Common records", conflict.common_comparable_records),
                ("Existing tail", conflict.existing_tail_comparable_records),
                ("Import tail", conflict.incoming_tail_comparable_records),
            ],
            indent="  ",
        ),
    ]


def format_import_duplicate_lines(duplicate: ImportDuplicateSession) -> list[CliLine]:
    lines: list[CliLine] = [
        cli_text(
            f"DUPLICATE {duplicate.session_id} - "
            f"{len(duplicate.source_paths)} input files with the same session id:",
            style=STYLE_ATTENTION,
        )
    ]
    lines.extend(
        cli_text(f"  {source_path}", style=STYLE_SECONDARY)
        for source_path in duplicate.source_paths
    )
    return lines


def format_import_failure_lines(failure: ImportFailure) -> list[CliLine]:
    return [
        cli_text("FAILED", style=STYLE_ERROR),
        *path_block_lines("Source", failure.source_path, indent="  "),
        cli_text("  Error:", style=STYLE_ATTENTION),
        *indented_text_lines(failure.message, indent="    "),
    ]


def import_title_update_plans(plan: ImportSessionsPlan) -> tuple[ImportSessionPlan, ...]:
    return tuple(
        import_plan
        for import_plan in (*plan.import_plans, *plan.fast_forward_plans)
        if import_plan.existing_index_thread_name is not None
        and import_plan.existing_index_thread_name != import_plan.thread_name
    )


def append_import_title_update_lines(
    lines: list[CliLine], plan: ImportSessionsPlan, tense: str
) -> None:
    title_updates = import_title_update_plans(plan)
    if not title_updates:
        return
    lines.append(cli_text(f"Titles {tense}:", style=STYLE_HEADING))
    for import_plan in title_updates:
        lines.extend(
            [
                cli_text(f"- {import_plan.session_id}", style=STYLE_SECONDARY),
                *labeled_lines(
                    [
                        ("From", import_plan.existing_index_thread_name),
                        ("To", import_plan.thread_name),
                    ],
                    indent="  ",
                ),
            ]
        )


def import_plan_has_errors(plan: ImportSessionsPlan) -> bool:
    return bool(plan.duplicates or plan.conflicts or plan.diverged or plan.failures)


def format_import_sessions_plan_lines(plan: ImportSessionsPlan) -> list[CliLine]:
    if (
        len(plan.import_plans) == 1
        and not plan.fast_forward_plans
        and not plan.skipped
        and not plan.skipped_equivalent
        and not plan.skipped_local_ahead
        and not plan.duplicates
        and not plan.conflicts
        and not plan.diverged
        and not plan.failures
    ):
        return format_import_plan_lines(plan.import_plans[0])

    lines: list[CliLine] = [
        count_text("Would import: ", len(plan.import_plans), style=STYLE_LABEL),
        count_text("Would fast-forward: ", len(plan.fast_forward_plans), style=STYLE_LABEL),
        count_text("Skipped (identical): ", len(plan.skipped), style=STYLE_LABEL),
        count_text("Skipped (equivalent): ", len(plan.skipped_equivalent), style=STYLE_LABEL),
        count_text("Skipped (local ahead): ", len(plan.skipped_local_ahead), style=STYLE_LABEL),
        count_text("Duplicates: ", len(plan.duplicates), style=STYLE_ATTENTION),
        count_text("ID conflicts: ", len(plan.conflicts), style=STYLE_ATTENTION),
        count_text("Diverged conflicts: ", len(plan.diverged), style=STYLE_ATTENTION),
        count_text("Failed: ", len(plan.failures), style=STYLE_ERROR),
    ]
    if plan.import_plans:
        lines.append(cli_text("Would import sessions:", style=STYLE_HEADING))
        for import_plan in plan.import_plans:
            lines.append(
                prefixed_session_info_text(
                    "- ",
                    session_display_info(
                        import_plan.session_id,
                        import_plan.thread_name,
                        import_plan.started_at,
                        import_plan.ended_at,
                    ),
                    prefix_style=STYLE_SECONDARY,
                )
            )
            lines.append(path_arrow_text(import_plan.target_path))
        lines.append(cli_text("State cache reset required after import.", style=STYLE_ATTENTION))
    if plan.fast_forward_plans:
        lines.append(cli_text("Would fast-forward sessions:", style=STYLE_HEADING))
        for import_plan in plan.fast_forward_plans:
            lines.append(
                prefixed_session_info_text(
                    "- ",
                    session_display_info(
                        import_plan.session_id,
                        import_plan.thread_name,
                        import_plan.started_at,
                        import_plan.ended_at,
                    ),
                    prefix_style=STYLE_SECONDARY,
                )
            )
            lines.append(path_arrow_text(import_plan.target_path))
        lines.append(
            cli_text("State cache reset required after fast-forward.", style=STYLE_ATTENTION)
        )
    if plan.skipped:
        lines.append(cli_text("Skipped (identical) sessions:", style=STYLE_HEADING))
        for identical_skip in plan.skipped:
            lines.extend(format_import_skipped_lines(identical_skip))
    if plan.skipped_equivalent:
        lines.append(cli_text("Skipped (equivalent) sessions:", style=STYLE_HEADING))
        for equivalent_skip in plan.skipped_equivalent:
            lines.extend(format_import_history_skip_lines(equivalent_skip, "equivalent"))
    if plan.skipped_local_ahead:
        lines.append(cli_text("Skipped (local ahead) sessions:", style=STYLE_HEADING))
        for local_ahead_skip in plan.skipped_local_ahead:
            lines.extend(format_import_history_skip_lines(local_ahead_skip, "local ahead"))
    if plan.duplicates:
        lines.append(cli_text("Duplicate input sessions:", style=STYLE_ATTENTION))
        for duplicate in plan.duplicates:
            lines.extend(format_import_duplicate_lines(duplicate))
    if plan.conflicts:
        lines.append(cli_text("ID conflicts:", style=STYLE_ATTENTION))
        for conflict in plan.conflicts:
            lines.extend(format_import_conflict_lines(conflict))
    if plan.diverged:
        lines.append(cli_text("Diverged conflicts:", style=STYLE_ATTENTION))
        for diverged_conflict in plan.diverged:
            lines.extend(format_import_diverged_lines(diverged_conflict))
    if plan.failures:
        lines.append(cli_text("Failed:", style=STYLE_ATTENTION))
        for failure in plan.failures:
            lines.extend(format_import_failure_lines(failure))
    append_import_title_update_lines(lines, plan, "would update")
    return lines


def print_single_import_result(result: ImportSessionsResult) -> None:
    plan = result.plan.import_plans[0]
    print_cli_line("Imported session:", style=STYLE_SUCCESS)
    print_cli_line(
        styled_session_display_text(
            session_display_info(plan.session_id, plan.thread_name, plan.started_at, plan.ended_at),
            sys.stdout.encoding,
        )
    )
    print_encoded_lines(
        [
            *path_block_lines("Source", plan.source_path, indent="  "),
            *path_block_lines("Target", plan.target_path, indent="  "),
            *labeled_lines([("Action", import_rollout_action_label(plan))], indent="  "),
            *labeled_lines(
                [("Index action", import_index_action_label(plan.index_action))],
                indent="  ",
            ),
        ]
    )
    if result.session_index_backup_path is not None:
        print_cli_line()
        print_path_backup_block(result.session_index_backup_path)


def print_import_plan_rows(
    heading: str,
    prefix: str,
    plans: Sequence[ImportSessionPlan],
    *,
    style: str,
) -> None:
    if not plans:
        return
    print_cli_line(heading, style=style)
    for plan in plans:
        print_cli_line(
            prefixed_session_info_text(
                prefix,
                session_display_info(
                    plan.session_id,
                    plan.thread_name,
                    plan.started_at,
                    plan.ended_at,
                ),
                prefix_style=style,
            )
        )


def print_import_result_summary(plan: ImportSessionsPlan) -> None:
    print_cli_line("Summary:", style=STYLE_HEADING)
    summary_lines = (
        ("Sessions added: ", len(plan.import_plans), STYLE_SUCCESS),
        ("Fast-forwarded: ", len(plan.fast_forward_plans), STYLE_SUCCESS),
        ("Skipped (identical): ", len(plan.skipped), STYLE_LABEL),
        ("Skipped (equivalent): ", len(plan.skipped_equivalent), STYLE_LABEL),
        ("Skipped (local ahead): ", len(plan.skipped_local_ahead), STYLE_LABEL),
        ("Duplicates: ", len(plan.duplicates), STYLE_ATTENTION),
        ("ID conflicts: ", len(plan.conflicts), STYLE_ATTENTION),
        ("Diverged conflicts: ", len(plan.diverged), STYLE_ATTENTION),
        ("Failed: ", len(plan.failures), STYLE_ERROR),
    )
    for prefix, count, style in summary_lines:
        print_cli_line(count_text(prefix, count, style=style))


def print_import_sessions_result(result: ImportSessionsResult) -> None:
    plan = result.plan
    if (
        len(plan.import_plans) == 1
        and not plan.fast_forward_plans
        and not plan.skipped
        and not plan.skipped_equivalent
        and not plan.skipped_local_ahead
        and not plan.duplicates
        and not plan.conflicts
        and not plan.diverged
        and not plan.failures
    ):
        print_single_import_result(result)
        return

    print_import_plan_rows(
        "Added sessions:",
        "Added: ",
        plan.import_plans,
        style=STYLE_SUCCESS_STRONG,
    )
    print_import_plan_rows(
        "Fast-forwarded sessions:",
        "Fast-forwarded: ",
        plan.fast_forward_plans,
        style=STYLE_SUCCESS_STRONG,
    )
    if plan.skipped:
        print_cli_line("Skipped (identical) sessions:", style=STYLE_LABEL)
        for skipped in plan.skipped:
            print_encoded_lines(format_import_skipped_lines(skipped))
    if plan.skipped_equivalent:
        print_cli_line("Skipped (equivalent) sessions:", style=STYLE_LABEL)
        for history_skip in plan.skipped_equivalent:
            print_encoded_lines(format_import_history_skip_lines(history_skip, "equivalent"))
    if plan.skipped_local_ahead:
        print_cli_line("Skipped (local ahead) sessions:", style=STYLE_LABEL)
        for history_skip in plan.skipped_local_ahead:
            print_encoded_lines(format_import_history_skip_lines(history_skip, "local ahead"))
    if plan.duplicates:
        print_cli_line("Duplicate input sessions:", style=STYLE_ATTENTION)
        for duplicate in plan.duplicates:
            print_encoded_lines(format_import_duplicate_lines(duplicate))
    if plan.conflicts:
        print_cli_line("ID conflicts:", style=STYLE_ATTENTION)
        for conflict in plan.conflicts:
            print_encoded_lines(format_import_conflict_lines(conflict))
    if plan.diverged:
        print_cli_line("Diverged conflicts:", style=STYLE_ATTENTION)
        for diverged_conflict in plan.diverged:
            print_cli_lines(format_import_diverged_lines(diverged_conflict))
    if plan.failures:
        print_cli_line("Failed:", style=STYLE_ATTENTION)
        for failure in plan.failures:
            print_encoded_lines(format_import_failure_lines(failure))
    if result.session_index_backup_path is not None or result.rollout_backup_paths:
        print_cli_line()
        print_path_backup_block(result.session_index_backup_path, result.rollout_backup_paths)
    title_update_lines: list[CliLine] = []
    append_import_title_update_lines(title_update_lines, plan, "updated")
    print_cli_lines(title_update_lines)
    if any(
        (
            plan.import_plans,
            plan.fast_forward_plans,
            plan.skipped,
            plan.skipped_equivalent,
            plan.skipped_local_ahead,
            plan.duplicates,
            plan.conflicts,
            plan.diverged,
            plan.failures,
        )
    ):
        print_cli_line()
    print_import_result_summary(plan)


def run_import_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    sessions_dir = (
        args.sessions_dir.expanduser().resolve() if args.sessions_dir else codex_home / "sessions"
    )

    if args.dry_run:
        try:
            plan = plan_sessions_import(
                source_path=args.input,
                codex_home=codex_home,
                session_index_path=session_index_path,
                sessions_dir=sessions_dir,
                name=args.name,
                merge=args.merge,
            )
        except (CliError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print_cli_lines(format_import_sessions_plan_lines(plan))
        return 1 if import_plan_has_errors(plan) else 0

    try:
        result = import_sessions(
            source_path=args.input,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            name=args.name,
            merge=args.merge,
            reset_state_cache=not args.no_reset_state_cache,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print_import_sessions_result(result)
    if result.plan.import_plans or result.plan.fast_forward_plans:
        print_mutation_state_cache_status(
            codex_home,
            result.state_cache_backups,
            result.state_cache_reset_error,
            result.state_cache_reset_skipped,
            non_interactive=args.non_interactive,
        )
    return 1 if import_plan_has_errors(result.plan) else 0


def export_rollout_action_label(plan: ExportSessionPlan) -> str:
    if plan.rollout_will_be_rewritten:
        return "copy with rollout title event update"
    return "copy unchanged"


def format_export_session_plan_lines(plan: ExportSessionPlan) -> list[CliLine]:
    lines: list[CliLine] = [
        styled_session_display_text(
            session_display_info(plan.session_id, plan.thread_name, plan.started_at, plan.ended_at),
            sys.stdout.encoding,
        ),
        *path_block_lines("Source", plan.source_path, indent="  "),
        *path_block_lines("Output", plan.output_path, indent="  "),
        *labeled_lines([("Action", export_rollout_action_label(plan))], indent="  "),
    ]
    if plan.overwrite:
        lines.extend(labeled_lines([("Overwrite", "yes")]))
    return lines


def export_destination_label(bundle_plan: ExportSessionsPlan, plan: ExportSessionPlan) -> str:
    if bundle_plan.output_kind == EXPORT_OUTPUT_ZIP:
        return f"{bundle_plan.output_path}!{plan.output_path.as_posix()}"
    return str(plan.output_path)


def format_export_plan_lines(plan: ExportSessionsPlan) -> list[CliLine]:
    if (
        len(plan.session_plans) == 1
        and plan.output_kind != EXPORT_OUTPUT_ZIP
        and not plan.filtered_out_count
    ):
        return format_export_session_plan_lines(plan.session_plans[0])

    lines: list[CliLine] = [
        count_text("Sessions selected: ", len(plan.session_plans), style=STYLE_LABEL)
    ]
    if plan.filtered_out_count:
        lines.append(
            count_text(
                "Sessions filtered out: ",
                plan.filtered_out_count,
                style=STYLE_SECONDARY,
            )
        )
    if plan.output_kind == EXPORT_OUTPUT_ZIP:
        lines.extend(path_block_lines("Output zip", plan.output_path))
        if plan.output_path is not None and plan.output_path.exists() and plan.force:
            lines.extend(labeled_lines([("Overwrite zip", "yes")]))
    elif plan.output_kind == EXPORT_OUTPUT_DIRECTORY:
        lines.extend(path_block_lines("Output directory", plan.output_path))

    lines.append(cli_text("Would export:", style=STYLE_HEADING))
    for session_plan in plan.session_plans:
        lines.append(
            prefixed_session_info_text(
                "- ",
                session_display_info(
                    session_plan.session_id,
                    session_plan.thread_name,
                    session_plan.started_at,
                    session_plan.ended_at,
                ),
                prefix_style=STYLE_SECONDARY,
            )
        )
        lines.append(path_arrow_text(export_destination_label(plan, session_plan)))
    return lines


def run_export_command(args: argparse.Namespace) -> int:
    codex_home = args.codex_home.expanduser().resolve()
    session_index_path = (
        args.session_index.expanduser().resolve()
        if args.session_index
        else codex_home / "session_index.jsonl"
    )
    sessions_dir = (
        args.sessions_dir.expanduser().resolve() if args.sessions_dir else codex_home / "sessions"
    )

    if args.dry_run:
        try:
            plan = plan_sessions_export(
                targets=args.targets,
                codex_home=codex_home,
                output=args.output,
                session_index_path=session_index_path,
                sessions_dir=sessions_dir,
                all_sessions=args.all,
                only=args.only,
                exclude=args.exclude,
                started_after=args.started_after,
                started_before=args.started_before,
                updated_after=args.updated_after,
                updated_before=args.updated_before,
                force=args.force,
            )
        except (CliError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print_cli_lines(format_export_plan_lines(plan))
        return 0

    try:
        result = export_sessions(
            targets=args.targets,
            codex_home=codex_home,
            output=args.output,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            all_sessions=args.all,
            only=args.only,
            exclude=args.exclude,
            started_after=args.started_after,
            started_before=args.started_before,
            updated_after=args.updated_after,
            updated_before=args.updated_before,
            force=args.force,
        )
    except (CliError, ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    plan = result.plan
    if len(plan.session_plans) != 1:
        print_cli_line(
            count_text(
                "Exported sessions: ",
                len(plan.session_plans),
                style=STYLE_SUCCESS,
            )
        )
        if plan.filtered_out_count:
            print_cli_line(
                count_text(
                    "Sessions filtered out: ",
                    plan.filtered_out_count,
                    style=STYLE_SECONDARY,
                )
            )
        if plan.output_kind == EXPORT_OUTPUT_ZIP:
            print_encoded_lines(path_block_lines("Output zip", plan.output_path))
        elif plan.output_kind == EXPORT_OUTPUT_DIRECTORY:
            print_encoded_lines(path_block_lines("Output directory", plan.output_path))
        return 0

    session_plan = plan.session_plans[0]
    print_cli_line(
        prefixed_session_info_text(
            "Exported: ",
            session_display_info(
                session_plan.session_id,
                session_plan.thread_name,
                session_plan.started_at,
                session_plan.ended_at,
            ),
            prefix_style=STYLE_SUCCESS,
        )
    )
    if plan.filtered_out_count:
        print_cli_line(f"Sessions filtered out: {plan.filtered_out_count}")
    destination = export_destination_label(plan, session_plan)
    print_encoded_lines(
        [
            *path_block_lines("Source", session_plan.source_path, indent="  "),
            *path_block_lines("Output", destination, indent="  "),
            *labeled_lines([("Action", export_rollout_action_label(session_plan))], indent="  "),
        ]
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    prog = cli_prog_from_argv0()
    if raw_argv[:1] == ["list"]:
        return run_list_command(parse_list_args(raw_argv[1:], prog))
    if raw_argv[:1] in (["find"], ["grep"]):
        return run_search_command(parse_search_args(raw_argv[0], raw_argv[1:], prog))
    if raw_argv[:1] == ["repair-index"]:
        return run_repair_index_command(parse_repair_index_args(raw_argv[1:], prog))
    if raw_argv[:1] == ["rename"]:
        return run_rename_command(parse_rename_args(raw_argv[1:], prog))
    if raw_argv[:1] == ["import"]:
        return run_import_command(parse_import_args(raw_argv[1:], prog))
    if raw_argv[:1] == ["export"]:
        return run_export_command(parse_export_args(raw_argv[1:], prog))
    if raw_argv[:1] == ["reset-state-cache"]:
        return run_reset_state_cache_command(parse_reset_state_cache_args(raw_argv[1:], prog))

    args = parse_args(raw_argv, prog)
    codex_home = args.codex_home.expanduser().resolve()
    try:
        markdown_features = parse_markdown_include(args.md_include)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.md_tool_preview_chars < 1:
        raise SystemExit("--md-tool-preview-chars must be greater than zero")

    output_format = infer_output_format(args)
    try:
        conversion_input = resolve_conversion_input(args.input, codex_home)
    except CliError as exc:
        raise SystemExit(str(exc)) from exc

    input_path = conversion_input.path
    output_path = resolve_output_path(
        args.output,
        input_path,
        codex_home,
        output_format,
        conversion_input.output_stem,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_format == "md":
        tool_mode = resolve_markdown_tool_mode(markdown_features, args.md_tools)
        try:
            count = convert_jsonl_to_markdown(
                input_path=input_path,
                output_path=output_path,
                options=MarkdownOptions(
                    tool_mode=tool_mode,
                    tool_preview_chars=args.md_tool_preview_chars,
                    include_metadata="metadata" in markdown_features,
                    include_raw="raw" in markdown_features,
                    redaction=args.redact_encrypted,
                    image_mode=args.md_images,
                ),
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print_write_result(count, "Markdown sections", output_path)
        return 0

    try:
        count = convert_jsonl_to_yaml_stream(
            input_path=input_path,
            output_path=output_path,
            redaction=args.redact_encrypted,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print_write_result(count, "YAML documents", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
