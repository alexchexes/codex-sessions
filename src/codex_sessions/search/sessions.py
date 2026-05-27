import json
import os
from pathlib import Path
from typing import Any

from codex_sessions.errors import CliError
from codex_sessions.formats.markdown.message_content import (
    content_to_text,
    searchable_user_message_text,
)
from codex_sessions.formats.markdown.tools import (
    DEFAULT_TOOL_PREVIEW_CHARS,
    normalized_tool_short_name,
    parse_json_object_maybe,
    render_smart_tool_call_preview,
    tool_display_name,
    truncate_preview,
)
from codex_sessions.search.cache import (
    cached_search_document,
    prune_missing_search_cache_entries,
    read_search_cache,
    search_cache_entry,
    search_cache_key,
    search_cache_path,
    write_search_cache,
)
from codex_sessions.search.core import (
    SearchOptions,
    SearchResult,
    compile_search_pattern,
    search_matching_lines,
)
from codex_sessions.sessions.cache import (
    cached_session_metadata,
    prune_missing_session_cache_entries,
    read_session_cache,
    session_cache_entry_from_document,
    session_cache_key,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.display import (
    session_display_info_for_search,
    session_title_match_spans,
)
from codex_sessions.sessions.documents import (
    SearchDocument,
    infer_search_document_title,
)
from codex_sessions.sessions.documents import (
    build_search_document as build_session_document,
)
from codex_sessions.sessions.files import (
    SessionFile,
    discover_session_paths,
    format_session_file_path,
    session_id_from_path,
)
from codex_sessions.sessions.index import normalize_session_id, read_session_index


def parse_embedded_json(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def render_search_text(value: Any) -> str:
    if isinstance(value, str):
        parsed = parse_embedded_json(value)
        if parsed is not value:
            return render_search_text(parsed)
        return str(value)

    if isinstance(value, dict):
        lines = []
        for key, inner in value.items():
            rendered = render_search_text(inner)
            if not rendered:
                continue
            if "\n" in rendered:
                lines.extend([f"{key}:", rendered])
            else:
                lines.append(f"{key}: {rendered}")
        return "\n".join(lines)

    if isinstance(value, list):
        return "\n".join(rendered for item in value if (rendered := render_search_text(item)))

    if value is None:
        return ""
    return str(value)


def session_search_text(input_path: Path, options: SearchOptions) -> str:
    return "\n".join(session_search_lines(input_path, options))


def session_search_lines(input_path: Path, options: SearchOptions) -> list[str]:
    document = build_search_document(input_path, options.redaction)
    return search_document_lines(document, options)


def search_document_lines(document: SearchDocument, options: SearchOptions) -> list[str]:
    lines: list[str] = []
    seen_lines = set()
    line_groups = [document.visible_lines]
    if options.include_metadata:
        line_groups.append(document.metadata_lines)
    if options.include_tools:
        line_groups.append(document.tool_lines)

    for group in line_groups:
        for line in group:
            # Search output is a quick index, so repeated identical rollout lines add noise.
            if line and line not in seen_lines:
                seen_lines.add(line)
                lines.append(line)
    return lines


def build_search_document(input_path: Path, redaction: str) -> SearchDocument:
    return build_session_document(
        input_path,
        redaction,
        session_id_from_path=session_id_from_path,
        render_line_groups=render_search_line_groups,
    )


def render_search_line_groups(record: dict[str, Any]) -> list[tuple[str, list[str]]]:
    record_type = record.get("type")
    payload = record.get("payload")

    if record_type == "session_meta" and isinstance(payload, dict):
        return [("metadata", render_session_metadata_search_lines(payload))]

    if record_type == "response_item" and isinstance(payload, dict):
        payload_type = payload.get("type")
        if payload_type == "message":
            role = payload.get("role")
            text = content_to_text(payload.get("content"))
            if role == "assistant":
                return [("visible", render_labeled_search_lines("Codex", text))]
            if role == "user":
                searchable_text = searchable_user_message_text(text)
                if searchable_text:
                    return [("visible", render_labeled_search_lines("User", searchable_text))]
            return []
        if payload_type == "reasoning":
            return []
        if payload_type in {"function_call", "tool_search_call", "custom_tool_call"}:
            return [("tools", render_tool_call_search_lines(payload))]
        return []

    if record_type == "event_msg" and isinstance(payload, dict):
        payload_type = payload.get("type")
        if payload_type == "user_message":
            searchable_text = searchable_user_message_text(str(payload.get("message", "")))
            if searchable_text:
                return [("visible", render_labeled_search_lines("User", searchable_text))]
            return []
        if payload_type == "agent_message":
            return [("visible", render_labeled_search_lines("Codex", payload.get("message", "")))]

    return []


def render_search_lines(record: dict[str, Any], options: SearchOptions) -> list[str]:
    lines = []
    for group, group_lines in render_search_line_groups(record):
        if group == "metadata" and not options.include_metadata:
            continue
        if group == "tools" and not options.include_tools:
            continue
        lines.extend(group_lines)
    return lines


def render_labeled_search_lines(label: str, text: str) -> list[str]:
    normalized_text = text.strip()
    if not normalized_text:
        return []
    return [f"{label}: {line.strip()}" for line in normalized_text.splitlines() if line.strip()]


def render_session_metadata_search_lines(payload: dict[str, Any]) -> list[str]:
    lines = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        lines.append(f"Session metadata: cwd: {cwd}")

    git = payload.get("git")
    if isinstance(git, dict):
        branch = git.get("branch")
        if isinstance(branch, str) and branch:
            lines.append(f"Session metadata: branch: {branch}")
        repository_url = git.get("repository_url")
        if isinstance(repository_url, str) and repository_url:
            lines.append(f"Session metadata: repository_url: {repository_url}")

    return lines


def render_tool_call_search_lines(payload: dict[str, Any]) -> list[str]:
    full_name = tool_display_name(payload)
    arguments = payload.get("arguments") if "arguments" in payload else payload.get("input")
    short_name = normalized_tool_short_name(full_name)

    args = parse_json_object_maybe(arguments)
    if short_name == "shell_command" and args is not None:
        command = args.get("command")
        if command:
            return render_labeled_search_lines(f"Tool call: {full_name}", str(command))
        return [f"Tool call: {full_name}"]

    if short_name == "apply_patch" and arguments:
        patch_preview = truncate_preview(str(arguments), DEFAULT_TOOL_PREVIEW_CHARS)
        return render_labeled_search_lines(f"Tool call: {full_name}", patch_preview)

    preview = render_smart_tool_call_preview(full_name, arguments, DEFAULT_TOOL_PREVIEW_CHARS)
    if not preview:
        return [f"Tool call: {full_name}"]

    preview_lines = [
        line
        for line in preview
        if not line.startswith("Workdir:")
        and not line.startswith("Timeout ms:")
        and not line.startswith("Call ID:")
    ]
    return [
        f"Tool call: {full_name}: {line.strip()}"
        for line in preview_lines
        if line.strip() and not line.startswith("```")
    ]


def search_document_for_file(
    path: Path,
    redaction: str,
    cache_entries: dict[str, Any] | None,
    *,
    rebuild_cache: bool,
) -> tuple[SearchDocument, os.stat_result, bool]:
    stat_result = path.stat()
    cache_key = search_cache_key(path)
    if cache_entries is not None and not rebuild_cache:
        document = cached_search_document(
            cache_entries.get(cache_key), path, stat_result, redaction
        )
        if document is not None:
            return document, stat_result, False

    document = build_search_document(path, redaction)
    return document, stat_result, True


def load_search_documents(
    codex_home: Path,
    sessions_dir: Path,
    redaction: str,
    *,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> tuple[list[tuple[Path, SearchDocument]], list[str]]:
    session_paths = discover_session_paths(sessions_dir)
    cache_path = search_cache_path(codex_home)
    cache_entries = read_search_cache(cache_path) if use_cache else None
    metadata_cache_path = session_cache_path(codex_home)
    metadata_cache_entries = read_session_cache(metadata_cache_path) if use_cache else None
    cache_dirty = False
    metadata_cache_dirty = False
    documents: list[tuple[Path, SearchDocument]] = []
    warnings: list[str] = []

    for session_path in session_paths:
        try:
            document, stat_result, document_rebuilt = search_document_for_file(
                session_path,
                redaction,
                cache_entries,
                rebuild_cache=rebuild_cache,
            )
            if cache_entries is not None and document_rebuilt:
                cache_entries[search_cache_key(session_path)] = search_cache_entry(
                    session_path, stat_result, document, redaction
                )
                cache_dirty = True
            if metadata_cache_entries is not None:
                metadata_cache_key = session_cache_key(session_path)
                cached_metadata = (
                    None
                    if rebuild_cache
                    else cached_session_metadata(
                        metadata_cache_entries.get(metadata_cache_key), session_path, stat_result
                    )
                )
                if cached_metadata is None:
                    metadata_cache_entries[metadata_cache_key] = session_cache_entry_from_document(
                        session_path, stat_result, document
                    )
                    metadata_cache_dirty = True
            documents.append((session_path, document))
        except (OSError, ValueError) as exc:
            relative_path = format_session_file_path(session_path, sessions_dir)
            warnings.append(f"{relative_path}: {exc}")
            if cache_entries is not None:
                cache_entries.pop(search_cache_key(session_path), None)
                cache_dirty = True
            if metadata_cache_entries is not None:
                metadata_cache_entries.pop(session_cache_key(session_path), None)
                metadata_cache_dirty = True

    if cache_entries is not None:
        cache_dirty = prune_missing_search_cache_entries(cache_entries) or cache_dirty
        if cache_dirty:
            try:
                write_search_cache(cache_path, cache_entries)
            except OSError as exc:
                warnings.append(f"Could not write search cache {cache_path}: {exc}")
    if metadata_cache_entries is not None:
        # This neutral cache is seeded here because list/find already parse every rollout.
        metadata_cache_dirty = (
            prune_missing_session_cache_entries(metadata_cache_entries) or metadata_cache_dirty
        )
        if metadata_cache_dirty:
            try:
                write_session_cache(metadata_cache_path, metadata_cache_entries)
            except OSError as exc:
                warnings.append(f"Could not write session cache {metadata_cache_path}: {exc}")

    return documents, warnings


def search_sessions(
    codex_home: Path,
    options: SearchOptions,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
    *,
    use_cache: bool = True,
    rebuild_cache: bool = False,
) -> tuple[list[SearchResult], list[str]]:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    if not resolved_sessions_dir.exists():
        raise CliError(f"Sessions directory not found: {resolved_sessions_dir}")

    index_path = session_index_path or codex_home / "session_index.jsonl"
    index_entries = read_session_index(index_path)
    entries_by_id = {normalize_session_id(entry.session_id): entry for entry in index_entries}
    search_pattern = compile_search_pattern(options)
    documents, warnings = load_search_documents(
        codex_home=codex_home,
        sessions_dir=resolved_sessions_dir,
        redaction=options.redaction,
        use_cache=use_cache,
        rebuild_cache=rebuild_cache,
    )

    results = []
    for session_path, document in documents:
        search_lines = search_document_lines(document, options)
        all_lines = search_matching_lines(search_lines, search_pattern, options.line_width)
        if options.max_lines_per_session:
            lines = all_lines[: options.max_lines_per_session]
            omitted_occurrence_count = max(
                0,
                sum(line.occurrence_count for line in all_lines)
                - sum(line.occurrence_count for line in lines),
            )
        else:
            lines = all_lines
            omitted_occurrence_count = 0

        session_file = SessionFile(
            path=session_path,
            relative_path=format_session_file_path(session_path, resolved_sessions_dir),
            session_id=document.session_id,
            started_at=document.started_at,
            ended_at=document.ended_at,
        )
        inferred_title = infer_search_document_title(document)
        session = session_display_info_for_search(session_file, entries_by_id, inferred_title)
        session_title_matches = session_title_match_spans(session, search_pattern)
        if lines or session_title_matches:
            results.append(
                SearchResult(
                    session=session,
                    session_title_matches=session_title_matches,
                    lines=lines,
                    omitted_occurrence_count=omitted_occurrence_count,
                )
            )

    return results, warnings
