import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from codex_sessions.sessions.display import SessionDisplayInfo

MAX_MATCHES_BEFORE_LINE_OMISSION = 2
MAX_VISIBLE_MATCHES_PER_OMITTED_LINE = 1


@dataclass(frozen=True)
class SearchOptions:
    pattern: str
    regex: bool
    ignore_case: bool
    line_width: int
    max_lines_per_session: int
    include_metadata: bool
    include_tools: bool
    color: str
    redaction: str
    include_visible: bool = True
    include_tool_inputs: bool = False
    include_tool_outputs: bool = False
    include_titles: bool = True
    tool_include: frozenset[str] | None = None


@dataclass(frozen=True)
class SearchLine:
    text: str
    matches: tuple[tuple[int, int], ...]
    occurrence_count: int
    prefix_length: int = 0
    omission_note_start: int | None = None


@dataclass(frozen=True)
class SearchResult:
    session: SessionDisplayInfo
    session_title_matches: tuple[tuple[int, int], ...]
    lines: tuple[SearchLine, ...]
    omitted_occurrence_count: int


def compile_search_pattern(options: SearchOptions) -> re.Pattern[str]:
    if not options.pattern:
        raise ValueError("Search pattern must not be empty")
    flags = re.IGNORECASE if options.ignore_case else 0
    pattern = options.pattern if options.regex else re.escape(options.pattern)
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def search_matching_lines(
    lines: Sequence[str], search_pattern: re.Pattern[str], line_width: int
) -> tuple[SearchLine, ...]:
    matching_lines = []
    for line in lines:
        spans = match_spans(line, search_pattern)
        if spans:
            matching_lines.append(make_search_line(line, spans, line_width))
    return tuple(matching_lines)


def match_spans(text: str, search_pattern: re.Pattern[str]) -> tuple[tuple[int, int], ...]:
    return tuple(
        match.span() for match in search_pattern.finditer(text) if match.start() != match.end()
    )


def make_search_line(
    source_line: str,
    matches: Sequence[tuple[int, int]],
    line_width: int,
) -> SearchLine:
    width = max(20, line_width)
    occurrence_count = len(matches)
    source_prefix_end = search_line_prefix_end(source_line)
    if len(source_line) <= width:
        return SearchLine(
            text=source_line,
            matches=tuple(matches),
            occurrence_count=occurrence_count,
            prefix_length=max(0, source_prefix_end),
        )

    prefix_end = source_prefix_end
    if prefix_end == -1 or any(start < prefix_end for start, _ in matches):
        prefix_end = 0

    # Keep short labels like "User:" or "Tool call: shell_command:" stable while trimming.
    prefix = source_line[:prefix_end]
    if width - len(prefix) < 20:
        prefix = ""
        prefix_end = 0

    content = source_line[prefix_end:]
    content_matches = tuple((start - prefix_end, end - prefix_end) for start, end in matches)
    snippet, snippet_matches, omission_note_start = compact_line_content(
        content,
        content_matches,
        width - len(prefix),
    )
    adjusted_matches = tuple(
        (start + len(prefix), end + len(prefix)) for start, end in snippet_matches
    )
    return SearchLine(
        text=f"{prefix}{snippet}",
        matches=adjusted_matches,
        occurrence_count=occurrence_count,
        prefix_length=len(prefix),
        omission_note_start=(
            len(prefix) + omission_note_start if omission_note_start is not None else None
        ),
    )


def search_line_prefix_end(source_line: str) -> int:
    for tool_prefix in ("Tool call: ", "Tool output: "):
        if source_line.startswith(tool_prefix):
            second_separator = source_line.find(": ", len(tool_prefix))
            if second_separator != -1 and second_separator <= 72:
                return second_separator + 2

    prefix_end = source_line.find(": ")
    if prefix_end != -1 and prefix_end <= 48:
        return prefix_end + 2
    return -1


def compact_line_content(
    content: str,
    matches: Sequence[tuple[int, int]],
    width: int,
) -> tuple[str, tuple[tuple[int, int], ...], int | None]:
    if len(content) <= width:
        return content, tuple(matches), None
    if len(matches) == 1:
        snippet, snippet_matches = centered_match_snippet(content, matches[0], width)
        return snippet, snippet_matches, None
    # Many hits on one long line are usually less useful than one readable sample plus a count.
    if len(matches) > MAX_MATCHES_BEFORE_LINE_OMISSION:
        return compact_line_with_omission_note(content, matches, width)

    for context_chars in compact_context_sizes(width):
        chunks = merge_chunks(
            (
                max(0, start - context_chars),
                min(len(content), end + context_chars),
            )
            for start, end in matches
        )
        snippet, snippet_matches = compose_compact_chunks(content, chunks, matches)
        if len(snippet) <= width:
            return snippet, snippet_matches, None

    snippet, snippet_matches = centered_match_snippet(content, matches[0], width)
    return snippet, snippet_matches, None


def compact_line_with_omission_note(
    content: str,
    matches: Sequence[tuple[int, int]],
    width: int,
) -> tuple[str, tuple[tuple[int, int], ...], int]:
    visible_matches = tuple(matches[:MAX_VISIBLE_MATCHES_PER_OMITTED_LINE])
    omitted_count = len(matches) - len(visible_matches)
    note = f" ... (+{omitted_count} more on line)"
    available_width = width - len(note)
    if available_width < 20:
        note = f" (+{omitted_count} more)"
        available_width = max(1, width - len(note))

    snippet, snippet_matches = centered_match_snippet(content, visible_matches[0], available_width)
    return f"{snippet}{note}", snippet_matches, len(snippet)


def compact_context_sizes(width: int) -> tuple[int, ...]:
    candidates = (
        width,
        width * 3 // 4,
        width // 2,
        width // 3,
        96,
        80,
        64,
        48,
        40,
        32,
        24,
        16,
        10,
        6,
        3,
        0,
    )
    return tuple(sorted({max(0, candidate) for candidate in candidates}, reverse=True))


def merge_chunks(chunks: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in chunks:
        if not merged or start > merged[-1][1] + 5:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def compose_compact_chunks(
    content: str,
    chunks: Sequence[tuple[int, int]],
    matches: Sequence[tuple[int, int]],
) -> tuple[str, tuple[tuple[int, int], ...]]:
    parts = []
    adjusted_matches = []
    cursor = 0

    for index, (chunk_start, chunk_end) in enumerate(chunks):
        if index == 0 and chunk_start > 0:
            parts.append("...")
            cursor += 3
        elif index > 0:
            parts.append(" ... ")
            cursor += 5

        chunk_text = content[chunk_start:chunk_end]
        parts.append(chunk_text)
        for match_start, match_end in matches:
            visible_start = max(match_start, chunk_start)
            visible_end = min(match_end, chunk_end)
            if visible_start < visible_end:
                adjusted_matches.append(
                    (cursor + visible_start - chunk_start, cursor + visible_end - chunk_start)
                )
        cursor += len(chunk_text)

    if chunks and chunks[-1][1] < len(content):
        parts.append("...")

    return "".join(parts), tuple(adjusted_matches)


def centered_match_snippet(
    content: str, match: tuple[int, int], width: int
) -> tuple[str, tuple[tuple[int, int], ...]]:
    match_start, match_end = match
    prefix_marker = "..." if match_start > 0 else ""
    suffix_marker = "..." if match_end < len(content) else ""
    body_width = max(1, width - len(prefix_marker) - len(suffix_marker))
    match_length = match_end - match_start
    left_context = max(0, (body_width - match_length) // 2)
    start = max(0, match_start - left_context)
    end = min(len(content), start + body_width)
    if end - start < body_width:
        start = max(0, end - body_width)

    prefix_marker = "..." if start > 0 else ""
    suffix_marker = "..." if end < len(content) else ""
    snippet = f"{prefix_marker}{content[start:end]}{suffix_marker}"
    offset = len(prefix_marker) - start
    visible_start = max(match_start, start)
    visible_end = min(match_end, end)
    snippet_matches: tuple[tuple[int, int], ...] = ()
    if visible_start < visible_end:
        snippet_matches = ((visible_start + offset, visible_end + offset),)
    return snippet, snippet_matches
