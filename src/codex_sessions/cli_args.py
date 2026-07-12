import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from codex_sessions.formats.markdown.timing import (
    DEFAULT_GAP_THRESHOLD_SECONDS,
    DEFAULT_TOOL_DURATION_THRESHOLD_SECONDS,
)
from codex_sessions.formats.markdown.tools import DEFAULT_TOOL_PREVIEW_CHARS
from codex_sessions.sessions.files import ARCHIVE_SCOPES

MARKDOWN_FEATURES = {"tools", "metadata", "raw"}
MARKDOWN_TOOL_MODES = {"auto", "none", "names", "smart", "preview", "full"}
MARKDOWN_IMAGE_MODES = {"truncate", "extract", "inline"}
MARKDOWN_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?$", re.IGNORECASE)
SEARCH_TARGETS = {"visible", "metadata", "tool-inputs", "tool-outputs"}
SEARCH_TARGET_ALIASES = {
    "all": "all",
    "dialogue": "visible",
    "message": "visible",
    "messages": "visible",
    "visible": "visible",
    "meta": "metadata",
    "metadata": "metadata",
    "tool": "tools",
    "tools": "tools",
    "tool-call": "tool-inputs",
    "tool-calls": "tool-inputs",
    "tool-input": "tool-inputs",
    "tool-inputs": "tool-inputs",
    "tool-arg": "tool-inputs",
    "tool-args": "tool-inputs",
    "tool-argument": "tool-inputs",
    "tool-arguments": "tool-inputs",
    "tool-output": "tool-outputs",
    "tool-outputs": "tool-outputs",
    "tool-result": "tool-outputs",
    "tool-results": "tool-outputs",
}
MARKDOWN_PRESETS = {
    "dialogue": set(),
    "minimal": set(),
    "default": {"tools"},
    "tools": {"tools"},
    "metadata": {"tools", "metadata"},
    "full": {"tools", "metadata", "raw"},
}
DEFAULT_CLI_PROG = "codex-sessions"
CLI_PROG_ALIASES = {DEFAULT_CLI_PROG}
MARKDOWN_INCLUDE_ALIASES = {
    "all": "all",
    "none": "none",
    "tool": "tools",
    "tools": "tools",
    "tool-call": "tools",
    "tool-calls": "tools",
    "tool_call": "tools",
    "tool_calls": "tools",
    "meta": "metadata",
    "metadata": "metadata",
    "raw": "raw",
    "unhandled": "raw",
}


def cli_prog_from_argv0(argv0: str | None = None) -> str:
    stem = Path(sys.argv[0] if argv0 is None else argv0).stem
    if stem in CLI_PROG_ALIASES:
        return stem
    return DEFAULT_CLI_PROG


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def default_user_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def add_archives_arg(parser: argparse.ArgumentParser, *, default_scope: str) -> None:
    parser.add_argument(
        "--archives",
        choices=ARCHIVE_SCOPES,
        default=None,
        help=(
            f"Archive scope: exclude, include, or only archived sessions. Default: {default_scope}."
        ),
    )


def parse_list_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} list",
        description=(
            "List Codex sessions and cross-check session_index.jsonl against rollout JSONL files."
        ),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    add_archives_arg(parser, default_scope="exclude")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write the extracted session metadata cache.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Ignore existing cached session metadata and rewrite cache entries.",
    )
    return parser.parse_args(argv)


def parse_search_args(
    command: str, argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} {command}",
        description="Search Codex session rollout JSONL files.",
    )
    parser.add_argument("pattern", help="Text or regex pattern to search for.")
    parser.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Match case-insensitively.",
    )
    parser.add_argument(
        "-r",
        "-E",
        "--regex",
        action="store_true",
        help="Treat the pattern as a Python regular expression.",
    )
    parser.add_argument(
        "--line-width",
        type=int,
        default=160,
        metavar="N",
        help="Maximum visible characters per matching line. Default: %(default)s.",
    )
    parser.add_argument(
        "-m",
        "--max-lines-per-session",
        type=int,
        default=5,
        metavar="N",
        help=(
            "Maximum matching lines to show per session. Use 0 for no limit. Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Highlight matches with terminal colors. Default: auto.",
    )
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="Also search compact session metadata such as cwd, branch, and repository URL.",
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        help="Also search concise tool input and output previews.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search visible messages, compact metadata, and concise tool input/output previews.",
    )
    parser.add_argument(
        "--search-in",
        action="append",
        metavar="TARGETS",
        help=(
            "Search only selected targets. Comma-separated values: visible, metadata, "
            "tool-inputs, tool-outputs, tools, all. May be repeated."
        ),
    )
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="TARGET",
        help=(
            "Search only one session. TARGET may be a session ID, exact title, "
            "rollout path, or 'latest'. May be repeated."
        ),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    add_archives_arg(parser, default_scope="include")
    parser.add_argument(
        "--redact-encrypted",
        default="...",
        help="Replacement text for any encrypted_content field.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write the extracted search text cache.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Ignore existing cached search text and rewrite cache entries.",
    )
    return parser.parse_args(argv)


def parse_search_targets(specs: Sequence[str]) -> set[str]:
    targets: set[str] = set()
    for spec in specs:
        for raw_part in spec.split(","):
            part = raw_part.strip().lower().replace("_", "-")
            if not part:
                continue
            alias = SEARCH_TARGET_ALIASES.get(part)
            if alias is None:
                allowed = sorted(SEARCH_TARGET_ALIASES)
                raise ValueError(
                    f"Unknown --search-in target {raw_part!r}. Allowed values: {', '.join(allowed)}"
                )
            if alias == "all":
                targets.update(SEARCH_TARGETS)
            elif alias == "tools":
                targets.update({"tool-inputs", "tool-outputs"})
            else:
                targets.add(alias)
    if not targets:
        raise ValueError("--search-in must include at least one target")
    return targets


def parse_repair_index_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} repair-index",
        description="Repair missing session_index.jsonl entries for rollout JSONL files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show missing session_index.jsonl entries without modifying Codex state.",
    )
    add_state_cache_reset_control_args(parser)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or write the extracted session metadata cache.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Ignore existing cached session metadata and rewrite cache entries.",
    )
    return parser.parse_args(argv)


def parse_rename_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} rename",
        description="Rename a Codex session without automatically rebuilding its state database.",
    )
    parser.add_argument("target", help="Session ID or exact current session title.")
    parser.add_argument("name", nargs="+", help="New session title.")
    add_state_cache_reset_control_args(parser)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    return parser.parse_args(argv)


def parse_import_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} import",
        description="Import Codex rollout JSONL files into Codex home.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a rollout JSONL file, directory of JSONL files, or export zip.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the import plan without modifying Codex state.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Fast-forward existing sessions when imported rollout history is safely ahead.",
    )
    parser.add_argument(
        "--show-divergence",
        action="store_true",
        help="Show a compact preview of the first differing records for diverged imports.",
    )
    add_state_cache_reset_control_args(parser)
    parser.add_argument(
        "--name",
        "--rename",
        dest="name",
        help="Title to use for the imported session.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    return parser.parse_args(argv)


def add_state_cache_reset_control_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-reset-state-cache",
        action="store_true",
        help=("Do not offer an optional Codex state database rebuild after session changes."),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt after changes; never rebuild the Codex state database automatically.",
    )
    add_sqlite_home_arg(parser)


def add_sqlite_home_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sqlite-home",
        type=Path,
        help=(
            "Codex SQLite home override. Otherwise use config.toml sqlite_home, "
            "CODEX_SQLITE_HOME, then Codex home."
        ),
    )


def parse_reset_state_cache_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} reset-state-cache",
        description="Back up and rebuild Codex state database files (lossy recovery).",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    add_sqlite_home_arg(parser)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that all Codex writers are closed and rebuild without prompting.",
    )
    return parser.parse_args(argv)


def parse_install_skill_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} install-skill",
        description="Install or update the bundled Codex skill for the current user.",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=default_user_skills_dir(),
        help="User skills directory. Defaults to ~/.agents/skills.",
    )
    return parser.parse_args(argv)


def parse_sync_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} sync",
        description="Synchronize Codex sessions through a local folder.",
    )
    parser.add_argument(
        "sync_dir",
        type=Path,
        help="Local folder used as the shared sync store.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the sync plan without modifying Codex home or the sync folder.",
    )
    parser.add_argument(
        "--show-divergence",
        action="store_true",
        help="Show a compact preview of the first differing records for diverged imports.",
    )
    add_state_cache_reset_control_args(parser)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    return parser.parse_args(argv)


def parse_export_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{prog} export",
        description="Export Codex sessions as transferable rollout JSONL files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  codex-sessions export "Exact session title"\n'
            "  codex-sessions export 019de863-c167-7942-9e39-9a3291b9bf55 -o ./exports/\n"
            "  codex-sessions export --all -o ./exports/\n"
            "  codex-sessions export --all --except 019de863... -o ./exports/\n"
            "  codex-sessions export --updated-after 2026-05-01 -o ./exports.zip\n"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Session IDs or exact session titles to export.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Output .jsonl path, directory, or .zip path. "
            "Single-session exports default to a readable file in the current directory."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Export all sessions before applying filters.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="TARGET",
        help="Session ID or exact title to include. May be repeated.",
    )
    parser.add_argument(
        "--except",
        dest="exclude",
        action="append",
        default=[],
        metavar="TARGET",
        help="Session ID or exact title to exclude. May be repeated.",
    )
    parser.add_argument(
        "--started-after",
        metavar="TIMESTAMP",
        help="Keep sessions started at or after this ISO timestamp or YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--started-before",
        metavar="TIMESTAMP",
        help="Keep sessions started before this ISO timestamp or YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--updated-after",
        metavar="TIMESTAMP",
        help="Keep sessions last updated at or after this ISO timestamp or YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--updated-before",
        metavar="TIMESTAMP",
        help="Keep sessions last updated before this ISO timestamp or YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite colliding output files or replace an existing zip archive.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the export plan without writing anything.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--session-index",
        type=Path,
        help="Path to session_index.jsonl. Defaults to <codex-home>/session_index.jsonl.",
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        help="Path to Codex sessions directory. Defaults to <codex-home>/sessions.",
    )
    return parser.parse_args(argv)


def parse_args(
    argv: Sequence[str] | None = None, prog: str = DEFAULT_CLI_PROG
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Convert Codex session rollout JSONL files to YAML or Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  list       list sessions and cross-check session_index.jsonl with rollout files\n\n"
            "  find       search sessions under Codex home\n"
            "  grep       alias for find\n\n"
            "  repair-index\n"
            "             inspect missing session_index.jsonl entries\n\n"
            "  rename     rename a session_index.jsonl entry\n\n"
            "  import     import a bare rollout JSONL file\n\n"
            "  export     export sessions as rollout JSONL files\n\n"
            "  sync       synchronize sessions through a local folder\n\n"
            "  reset-state-cache\n"
            "             back up and rebuild the Codex state database (lossy recovery)\n\n"
            "  install-skill\n"
            "             install or update the bundled Codex skill\n\n"
            "Markdown include presets:\n"
            "  dialogue   visible user/Codex messages, reasoning, progress messages\n"
            "  default    dialogue plus tool calls and tool outputs\n"
            "  metadata   default plus metadata tables such as turn_context/token_count\n"
            "  full       metadata plus raw blocks for unhandled records\n\n"
            "Markdown tool detail modes:\n"
            "  auto       smart when tools are included by --md-include, otherwise none\n"
            "  none       omit tool call/output sections\n"
            "  names      show only tool names and call IDs\n"
            "  smart      show useful previews for known tool calls, otherwise names\n"
            "  preview    show names plus truncated arguments/outputs\n"
            "  full       show full arguments/outputs\n\n"
            "Markdown image modes:\n"
            "  truncate   replace base64 data images with compact placeholders\n"
            "  extract    write base64 data images next to the Markdown and link them\n"
            "  inline     keep base64 data images inline\n\n"
            "The --md-include value can also use modifiers, for example:\n"
            "  default,-tools\n"
            "  dialogue,+metadata\n"
            "  full,-raw\n\n"
            "Explicit --md-tools values override the tools setting from --md-include.\n"
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Rollout JSONL path, session ID, exact title, or 'latest'.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=("Path to the output file or directory. Defaults under <codex-home>/tmp."),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help=(
            "Codex home directory for session ID lookup and default output. "
            "Defaults to CODEX_HOME or ~/.codex."
        ),
    )
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "--format",
        choices=("yaml", "md", "markdown"),
        help="Output format. Defaults to Markdown for .md/.markdown output paths, otherwise YAML.",
    )
    format_group.add_argument(
        "--md",
        action="store_true",
        help="Write Markdown output without specifying an .md output path.",
    )
    format_group.add_argument(
        "--yaml",
        action="store_true",
        help="Write YAML output explicitly.",
    )
    parser.add_argument(
        "--md-include",
        default="default",
        metavar="SPEC",
        help="Markdown preset/modifiers controlling optional content. Default: default.",
    )
    parser.add_argument(
        "--md-tools",
        choices=tuple(sorted(MARKDOWN_TOOL_MODES)),
        default="auto",
        help="Markdown tool detail mode. Default: auto.",
    )
    parser.add_argument(
        "--md-tool-preview-chars",
        type=int,
        default=DEFAULT_TOOL_PREVIEW_CHARS,
        metavar="N",
        help=(
            "Maximum characters per tool argument/output preview when "
            "--md-tools=preview. Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--md-images",
        choices=tuple(sorted(MARKDOWN_IMAGE_MODES)),
        default="truncate",
        help="Markdown handling for base64 data images. Default: %(default)s.",
    )
    parser.add_argument(
        "--timestamps",
        action="store_true",
        help="Markdown: add local timestamps to rendered section headings.",
    )
    parser.add_argument(
        "--gap-threshold",
        type=parse_duration_arg_seconds,
        default=DEFAULT_GAP_THRESHOLD_SECONDS,
        metavar="DURATION",
        help=(
            "Markdown: insert a time-gap marker when rendered events are at least "
            "DURATION apart. Supports values like 30s, 5m, 4h. Default: 4h."
        ),
    )
    parser.add_argument(
        "--tool-duration-threshold",
        type=parse_duration_arg_seconds,
        default=DEFAULT_TOOL_DURATION_THRESHOLD_SECONDS,
        metavar="DURATION",
        help=(
            "Markdown: annotate tool outputs whose duration is at least DURATION. "
            "Use 0 to show all tool durations. Default: 30s."
        ),
    )
    parser.add_argument(
        "--redact-encrypted",
        default="...",
        help="Replacement text for any encrypted_content field.",
    )
    return parser.parse_args(argv)


def parse_duration_arg_seconds(value: str) -> float:
    normalized = value.strip().lower()
    match = MARKDOWN_DURATION_RE.fullmatch(normalized)
    if not match:
        raise argparse.ArgumentTypeError(
            "expected a non-negative duration such as 0, 30s, 5m, or 4h"
        )

    amount = float(match.group(1))
    unit = match.group(2) or "s"
    if unit == "ms":
        return amount / 1000
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 60 * 60
    raise argparse.ArgumentTypeError(f"unsupported duration unit: {unit}")


def parse_markdown_include(spec: str) -> set[str]:
    parts = [part.strip().lower() for part in spec.split(",") if part.strip()]
    if not parts:
        parts = ["default"]

    # The first token may be a preset; later tokens are always additive/removal modifiers.
    first = parts[0]
    if first in MARKDOWN_PRESETS:
        features = set(MARKDOWN_PRESETS[first])
        parts = parts[1:]
    else:
        alias = MARKDOWN_INCLUDE_ALIASES.get(first)
        if alias == "all":
            features = set(MARKDOWN_FEATURES)
            parts = parts[1:]
        elif alias == "none":
            features = set()
            parts = parts[1:]
        else:
            features = set(MARKDOWN_PRESETS["default"])

    for raw_part in parts:
        include = True
        part = raw_part
        if part.startswith("+"):
            part = part[1:]
        elif part.startswith("-"):
            include = False
            part = part[1:]

        alias = MARKDOWN_INCLUDE_ALIASES.get(part)
        if alias is None:
            allowed = sorted(set(MARKDOWN_PRESETS) | set(MARKDOWN_INCLUDE_ALIASES))
            raise ValueError(
                f"Unknown --md-include item {raw_part!r}. Allowed values: {', '.join(allowed)}"
            )
        if alias == "all":
            if include:
                features.update(MARKDOWN_FEATURES)
            else:
                features.clear()
            continue
        if alias == "none":
            if include:
                features.clear()
            continue
        if include:
            features.add(alias)
        else:
            features.discard(alias)

    return features


def resolve_markdown_tool_mode(markdown_features: set[str], requested_mode: str) -> str:
    if requested_mode == "auto":
        return "smart" if "tools" in markdown_features else "none"
    return requested_mode
