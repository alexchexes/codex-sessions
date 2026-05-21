import argparse
import errno
import os
import sys
from collections.abc import Sequence

from codex_sessions.cli_args import (
    cli_prog_from_argv0,
    parse_args,
    parse_export_args,
    parse_import_args,
    parse_list_args,
    parse_markdown_include,
    parse_rename_args,
    parse_repair_index_args,
    parse_search_args,
    resolve_markdown_tool_mode,
)
from codex_sessions.errors import CliError
from codex_sessions.formats.markdown.output import MarkdownOptions, convert_jsonl_to_markdown
from codex_sessions.formats.yaml import convert_jsonl_to_yaml_stream
from codex_sessions.search.core import SearchOptions
from codex_sessions.search.output import encode_for_output, render_search_results
from codex_sessions.search.sessions import (
    search_sessions,
)
from codex_sessions.sessions.display import (
    format_local_timestamp,
    local_timezone_offset_label,
)
from codex_sessions.sessions.index_workflows import (
    RepairIndexCandidate,
    list_session_lines_with_warnings,
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
    ImportDuplicateSession,
    ImportFailure,
    ImportSessionPlan,
    ImportSessionsPlan,
    ImportSessionsResult,
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


def format_repair_index_candidate(candidate: RepairIndexCandidate) -> str:
    updated_at = candidate.updated_at.isoformat() if candidate.updated_at else "UNKNOWN"
    return (
        f"{candidate.session_id} - {candidate.thread_name} - "
        f"{candidate.relative_path} - updated_at: {updated_at}"
    )


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
        lines, warnings = list_session_lines_with_warnings(
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            use_cache=not args.no_cache,
            rebuild_cache=args.rebuild_cache,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    for warning in warnings:
        print(
            encode_for_output(f"Warning: {warning}", sys.stderr.encoding),
            file=sys.stderr,
        )
    for line in lines:
        print(encode_for_output(line, sys.stdout.encoding))
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
            print(
                encode_for_output(f"Warning: {warning}", sys.stderr.encoding),
                file=sys.stderr,
            )
        print(f"Missing session_index.jsonl entries: {len(candidates)}")
        if candidates:
            print("Would add:")
            for candidate in candidates:
                print(
                    encode_for_output(format_repair_index_candidate(candidate), sys.stdout.encoding)
                )
            print("State cache reset required after repair.")
        else:
            print("No missing session_index.jsonl entries found.")
        if skipped_without_id:
            print(f"Skipped rollout files without session id: {skipped_without_id}")
        return 0

    try:
        result = repair_session_index(
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            use_cache=not args.no_cache,
            rebuild_cache=args.rebuild_cache,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    for warning in result.warnings:
        print(
            encode_for_output(f"Warning: {warning}", sys.stderr.encoding),
            file=sys.stderr,
        )
    print(f"Added session_index.jsonl entries: {len(result.candidates)}")
    if result.candidates:
        print("Added:")
        for candidate in result.candidates:
            print(encode_for_output(format_repair_index_candidate(candidate), sys.stdout.encoding))
        if result.session_index_backup_path is not None:
            print(f"Session index backup: {result.session_index_backup_path}")
        if result.state_cache_backups:
            print("State cache backups:")
            for backup in result.state_cache_backups:
                print(f"{backup.original_path} -> {backup.backup_path}")
        else:
            print("No Codex state cache files found to reset.")
    else:
        print("No missing session_index.jsonl entries found.")
    if result.skipped_without_id:
        print(f"Skipped rollout files without session id: {result.skipped_without_id}")
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
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    if not result.changed:
        print(
            encode_for_output(
                f"Session title already set: {result.session_id} - {result.new_thread_name}",
                sys.stdout.encoding,
            )
        )
        return 0

    print(encode_for_output(f"Renamed session: {result.session_id}", sys.stdout.encoding))
    if result.index_changed:
        print(encode_for_output(f"From: {result.old_thread_name}", sys.stdout.encoding))
    else:
        print(encode_for_output("Session index title was already set.", sys.stdout.encoding))
    print(encode_for_output(f"To: {result.new_thread_name}", sys.stdout.encoding))
    if result.rollout_changed:
        rollout_from = result.rollout_thread_name or "NO ROLLOUT TITLE EVENT"
        print(encode_for_output(f"Rollout title from: {rollout_from}", sys.stdout.encoding))
        if result.rollout_path is not None:
            print(f"Rollout file: {result.rollout_path}")
    if result.session_index_backup_path is not None:
        print(f"Session index backup: {result.session_index_backup_path}")
    if result.rollout_backup_path is not None:
        print(f"Rollout backup: {result.rollout_backup_path}")
    if result.state_cache_backups:
        print("State cache backups:")
        for backup in result.state_cache_backups:
            print(f"{backup.original_path} -> {backup.backup_path}")
    else:
        print("No Codex state cache files found to reset.")
    return 0


def import_index_action_label(action: str) -> str:
    if action == "add":
        return "add session_index.jsonl entry"
    if action == "update":
        return "update session_index.jsonl title"
    if action == "keep":
        return "keep existing session_index.jsonl entry"
    return action


def import_rollout_action_label(plan: ImportSessionPlan) -> str:
    if plan.rollout_will_be_rewritten:
        return "copy with rollout title event update"
    return "copy unchanged"


def format_import_plan_lines(plan: ImportSessionPlan) -> list[str]:
    lines = [
        f"Import source: {plan.source_path}",
        f"Session: {plan.session_id} - {plan.thread_name}",
        (
            "Started: "
            f"{format_local_timestamp(plan.started_at)} - "
            f"Updated: {format_local_timestamp(plan.ended_at)} "
            f"({local_timezone_offset_label(plan.ended_at or plan.started_at)})"
        ),
        f"Target rollout: {plan.target_path}",
        f"Source fingerprint: {format_fingerprint(plan.source_fingerprint)}",
        f"Index action: {import_index_action_label(plan.index_action)}",
        f"Rollout action: {import_rollout_action_label(plan)}",
        "State cache reset required after import.",
    ]
    if plan.existing_index_thread_name and plan.existing_index_thread_name != plan.thread_name:
        lines.insert(
            3,
            f"Existing session_index.jsonl title: {plan.existing_index_thread_name}",
        )
    return lines


def format_import_skipped_line(skipped: ImportSkippedSession) -> str:
    return (
        f"SKIPPED identical {skipped.session_id} - "
        f"Existing: {skipped.existing_path} ({format_fingerprint(skipped.fingerprint)}); "
        f"Import: {skipped.source_path}"
    )


def format_import_conflict_line(conflict: ImportConflict) -> str:
    existing_fingerprint = (
        format_fingerprint(conflict.existing_fingerprint)
        if conflict.existing_fingerprint
        else "UNKNOWN"
    )
    return (
        f"CONFLICT {conflict.session_id} - "
        f"Existing: {conflict.existing_path} ({existing_fingerprint}); "
        f"Import: {conflict.source_path} ({format_fingerprint(conflict.source_fingerprint)})"
    )


def format_import_duplicate_lines(duplicate: ImportDuplicateSession) -> list[str]:
    lines = [
        f"DUPLICATE {duplicate.session_id} - "
        f"{len(duplicate.source_paths)} input files with the same session id:"
    ]
    lines.extend(f"  {source_path}" for source_path in duplicate.source_paths)
    return lines


def format_import_failure_line(failure: ImportFailure) -> str:
    return f"FAILED {failure.source_path} - {failure.message}"


def import_plan_has_errors(plan: ImportSessionsPlan) -> bool:
    return bool(plan.duplicates or plan.conflicts or plan.failures)


def format_import_sessions_plan_lines(plan: ImportSessionsPlan) -> list[str]:
    if (
        len(plan.import_plans) == 1
        and not plan.skipped
        and not plan.duplicates
        and not plan.conflicts
        and not plan.failures
    ):
        return format_import_plan_lines(plan.import_plans[0])

    lines = [
        f"Would import: {len(plan.import_plans)}",
        f"Skipped identical: {len(plan.skipped)}",
        f"Duplicates: {len(plan.duplicates)}",
        f"Conflicts: {len(plan.conflicts)}",
        f"Failed: {len(plan.failures)}",
    ]
    if plan.import_plans:
        lines.append("Would import sessions:")
        for import_plan in plan.import_plans:
            lines.append(
                f"- {import_plan.session_id} - {import_plan.thread_name} -> "
                f"{import_plan.target_path}"
            )
        lines.append("State cache reset required after import.")
    if plan.skipped:
        lines.append("Skipped identical sessions:")
        lines.extend(format_import_skipped_line(skipped) for skipped in plan.skipped)
    if plan.duplicates:
        lines.append("Duplicate input sessions:")
        for duplicate in plan.duplicates:
            lines.extend(format_import_duplicate_lines(duplicate))
    if plan.conflicts:
        lines.append("Conflicts:")
        lines.extend(format_import_conflict_line(conflict) for conflict in plan.conflicts)
    if plan.failures:
        lines.append("Failed:")
        lines.extend(format_import_failure_line(failure) for failure in plan.failures)
    return lines


def print_single_import_result(result: ImportSessionsResult) -> None:
    plan = result.plan.import_plans[0]
    print(
        encode_for_output(
            f"Imported session: {plan.session_id} - {plan.thread_name}",
            sys.stdout.encoding,
        )
    )
    print(
        encode_for_output(f"Rollout: {plan.source_path} -> {plan.target_path}", sys.stdout.encoding)
    )
    print(
        encode_for_output(
            f"Index action: {import_index_action_label(plan.index_action)}", sys.stdout.encoding
        )
    )
    print(
        encode_for_output(
            f"Rollout action: {import_rollout_action_label(plan)}", sys.stdout.encoding
        )
    )
    if result.session_index_backup_path is not None:
        print(f"Session index backup: {result.session_index_backup_path}")
    if result.state_cache_backups:
        print("State cache backups:")
        for backup in result.state_cache_backups:
            print(f"{backup.original_path} -> {backup.backup_path}")
    else:
        print("No Codex state cache files found to reset.")


def print_import_sessions_result(result: ImportSessionsResult) -> None:
    plan = result.plan
    if (
        len(plan.import_plans) == 1
        and not plan.skipped
        and not plan.duplicates
        and not plan.conflicts
        and not plan.failures
    ):
        print_single_import_result(result)
        return

    print(f"Imported: {len(plan.import_plans)}")
    print(f"Skipped identical: {len(plan.skipped)}")
    print(f"Duplicates: {len(plan.duplicates)}")
    print(f"Conflicts: {len(plan.conflicts)}")
    print(f"Failed: {len(plan.failures)}")
    if plan.import_plans:
        print("Imported sessions:")
        for import_plan in plan.import_plans:
            print(
                encode_for_output(
                    f"- {import_plan.session_id} - {import_plan.thread_name} -> "
                    f"{import_plan.target_path}",
                    sys.stdout.encoding,
                )
            )
    if plan.skipped:
        print("Skipped identical sessions:")
        for skipped in plan.skipped:
            print(encode_for_output(format_import_skipped_line(skipped), sys.stdout.encoding))
    if plan.duplicates:
        print("Duplicate input sessions:")
        for duplicate in plan.duplicates:
            for line in format_import_duplicate_lines(duplicate):
                print(encode_for_output(line, sys.stdout.encoding))
    if plan.conflicts:
        print("Conflicts:")
        for conflict in plan.conflicts:
            print(encode_for_output(format_import_conflict_line(conflict), sys.stdout.encoding))
    if plan.failures:
        print("Failed:")
        for failure in plan.failures:
            print(encode_for_output(format_import_failure_line(failure), sys.stdout.encoding))
    if result.session_index_backup_path is not None:
        print(f"Session index backup: {result.session_index_backup_path}")
    if result.state_cache_backups:
        print("State cache backups:")
        for backup in result.state_cache_backups:
            print(f"{backup.original_path} -> {backup.backup_path}")
    elif plan.import_plans:
        print("No Codex state cache files found to reset.")


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
            )
        except (CliError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        for line in format_import_sessions_plan_lines(plan):
            print(encode_for_output(line, sys.stdout.encoding))
        return 1 if import_plan_has_errors(plan) else 0

    try:
        result = import_sessions(
            source_path=args.input,
            codex_home=codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
            name=args.name,
        )
    except (CliError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print_import_sessions_result(result)
    return 1 if import_plan_has_errors(result.plan) else 0


def export_rollout_action_label(plan: ExportSessionPlan) -> str:
    if plan.rollout_will_be_rewritten:
        return "copy with rollout title event update"
    return "copy unchanged"


def format_export_session_plan_lines(plan: ExportSessionPlan) -> list[str]:
    lines = [
        f"Export source: {plan.source_path}",
        f"Session: {plan.session_id} - {plan.thread_name}",
        (
            "Started: "
            f"{format_local_timestamp(plan.started_at)} - "
            f"Updated: {format_local_timestamp(plan.ended_at)} "
            f"({local_timezone_offset_label(plan.ended_at or plan.started_at)})"
        ),
        f"Output rollout: {plan.output_path}",
        f"Rollout action: {export_rollout_action_label(plan)}",
    ]
    if plan.overwrite:
        lines.append("Overwrite: yes")
    return lines


def export_destination_label(bundle_plan: ExportSessionsPlan, plan: ExportSessionPlan) -> str:
    if bundle_plan.output_kind == EXPORT_OUTPUT_ZIP:
        return f"{bundle_plan.output_path}!{plan.output_path.as_posix()}"
    return str(plan.output_path)


def format_export_plan_lines(plan: ExportSessionsPlan) -> list[str]:
    if (
        len(plan.session_plans) == 1
        and plan.output_kind != EXPORT_OUTPUT_ZIP
        and not plan.filtered_out_count
    ):
        return format_export_session_plan_lines(plan.session_plans[0])

    lines = [f"Sessions selected: {len(plan.session_plans)}"]
    if plan.filtered_out_count:
        lines.append(f"Sessions filtered out: {plan.filtered_out_count}")
    if plan.output_kind == EXPORT_OUTPUT_ZIP:
        lines.append(f"Output zip: {plan.output_path}")
        if plan.output_path is not None and plan.output_path.exists() and plan.force:
            lines.append("Overwrite zip: yes")
    elif plan.output_kind == EXPORT_OUTPUT_DIRECTORY:
        lines.append(f"Output directory: {plan.output_path}")

    lines.append("Would export:")
    for session_plan in plan.session_plans:
        lines.append(
            f"- {session_plan.session_id} - {session_plan.thread_name} -> "
            f"{export_destination_label(plan, session_plan)}"
        )
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
        for line in format_export_plan_lines(plan):
            print(encode_for_output(line, sys.stdout.encoding))
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
        print(f"Exported sessions: {len(plan.session_plans)}")
        if plan.filtered_out_count:
            print(f"Sessions filtered out: {plan.filtered_out_count}")
        if plan.output_kind == EXPORT_OUTPUT_ZIP:
            print(f"Output zip: {plan.output_path}")
        elif plan.output_kind == EXPORT_OUTPUT_DIRECTORY:
            print(f"Output directory: {plan.output_path}")
        for session_plan in plan.session_plans:
            print(
                encode_for_output(
                    f"- {session_plan.session_id} - {session_plan.thread_name} -> "
                    f"{export_destination_label(plan, session_plan)}",
                    sys.stdout.encoding,
                )
            )
        return 0

    session_plan = plan.session_plans[0]
    print(
        encode_for_output(
            f"Exported session: {session_plan.session_id} - {session_plan.thread_name}",
            sys.stdout.encoding,
        )
    )
    if plan.filtered_out_count:
        print(f"Sessions filtered out: {plan.filtered_out_count}")
    destination = export_destination_label(plan, session_plan)
    print(
        encode_for_output(
            f"Rollout: {session_plan.source_path} -> {destination}", sys.stdout.encoding
        )
    )
    print(
        encode_for_output(
            f"Rollout action: {export_rollout_action_label(session_plan)}", sys.stdout.encoding
        )
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
        print(f"Wrote {count} Markdown sections to {output_path}")
        return 0

    try:
        count = convert_jsonl_to_yaml_stream(
            input_path=input_path,
            output_path=output_path,
            redaction=args.redact_encrypted,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote {count} YAML documents to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
