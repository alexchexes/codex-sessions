import sys
from collections.abc import Sequence

from rich.text import Text

from codex_sessions.core.terminal import encode_for_output, terminal_console
from codex_sessions.search.core import SearchLine, SearchResult
from codex_sessions.sessions.display import styled_session_display_text


def text_spans_with_highlights(
    text: str,
    spans: Sequence[tuple[int, int]],
    encoding: str | None,
    *,
    base_style: str,
    match_style: str = "bold bright_red",
) -> Text:
    rendered = Text()
    position = 0
    for start, end in spans:
        rendered.append(encode_for_output(text[position:start], encoding), style=base_style)
        rendered.append(encode_for_output(text[start:end], encoding), style=match_style)
        position = end
    rendered.append(encode_for_output(text[position:], encoding), style=base_style)
    return rendered


def text_with_highlights(line: SearchLine, encoding: str | None) -> Text:
    rendered = text_spans_with_highlights(
        line.text,
        line.matches,
        encoding,
        base_style="",
    )
    if line.prefix_length:
        rendered.stylize("bright_blue", 0, line.prefix_length)
    if line.omission_note_start is not None:
        rendered.stylize("bright_black", line.omission_note_start, len(line.text))
    return rendered


def render_search_results(
    results: Sequence[SearchResult], warnings: Sequence[str], color: str
) -> None:
    console = terminal_console(sys.stdout, color=color)
    error_console = terminal_console(sys.stderr, color=color)

    for warning in warnings:
        error_console.print(
            Text(
                encode_for_output(f"Warning: {warning}", sys.stderr.encoding),
                style="yellow",
            ),
            soft_wrap=True,
        )

    for result_index, result in enumerate(results):
        if result_index:
            console.print()
        session_info_text = styled_session_display_text(
            result.session,
            sys.stdout.encoding,
            title_style="bold bright_white",
            title_matches=result.session_title_matches,
        )
        console.print(session_info_text, soft_wrap=True)
        for line in result.lines:
            rendered_line = Text()
            rendered_line.append("  ", style="dim")
            rendered_line.append_text(text_with_highlights(line, sys.stdout.encoding))
            console.print(rendered_line, soft_wrap=True)
        if result.omitted_occurrence_count:
            console.print(
                Text(
                    (
                        f"  (+{result.omitted_occurrence_count} more occurrences; "
                        "use --max-lines-per-session 0 to show all)"
                    ),
                    style="dim",
                ),
                soft_wrap=True,
            )
