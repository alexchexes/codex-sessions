import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from rich.console import Console
from rich.text import Text

from codex_sessions.search.core import SearchLine, SearchResult


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
    return text_spans_with_highlights(
        line.text,
        line.matches,
        encoding,
        base_style="dim",
    )


def env_flag_enabled(value: str | None) -> bool:
    return value is not None and value != "" and value != "0"


def auto_color_disabled(environ: Mapping[str, str]) -> bool:
    return "NO_COLOR" in environ or environ.get("CLICOLOR") == "0"


def auto_color_forced(environ: Mapping[str, str]) -> bool:
    return env_flag_enabled(environ.get("FORCE_COLOR")) or env_flag_enabled(
        environ.get("CLICOLOR_FORCE")
    )


def is_msys_terminal_environment(environ: Mapping[str, str]) -> bool:
    term = environ.get("TERM")
    if not term or term == "dumb":
        return False
    return any(
        environ.get(name)
        for name in (
            "MSYSTEM",
            "MINGW_CHOST",
            "MINTTY_PID",
            "TERM_PROGRAM",
        )
    )


def is_windows_pipe_stream(stream: TextIO) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
    except (AttributeError, OSError, ValueError):
        return False
    if handle == -1:
        return False
    file_type = ctypes.windll.kernel32.GetFileType(handle)
    return bool(file_type == 0x0003)


def console_color_options(
    color: str,
    stream: TextIO,
    environ: Mapping[str, str] = os.environ,
) -> tuple[bool | None, bool | None]:
    if color == "always":
        return True, False
    if color == "never":
        return False, True
    if auto_color_disabled(environ):
        return None, True
    if auto_color_forced(environ):
        return True, False
    if is_msys_terminal_environment(environ) and is_windows_pipe_stream(stream):
        return True, False
    return None, False


def render_search_results(
    results: Sequence[SearchResult], warnings: Sequence[str], color: str
) -> None:
    stdout_force_terminal, stdout_no_color = console_color_options(color, sys.stdout)
    stderr_force_terminal, stderr_no_color = console_color_options(color, sys.stderr)
    console = Console(
        file=sys.stdout,
        force_terminal=stdout_force_terminal,
        no_color=stdout_no_color,
        highlight=False,
        legacy_windows=False,
    )
    error_console = Console(
        file=sys.stderr,
        force_terminal=stderr_force_terminal,
        no_color=stderr_no_color,
        highlight=False,
        legacy_windows=False,
    )

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
        if result.session_info_matches:
            session_info_text = text_spans_with_highlights(
                result.session_info,
                result.session_info_matches,
                sys.stdout.encoding,
                base_style="bold",
            )
        else:
            session_info_text = Text(
                encode_for_output(result.session_info, sys.stdout.encoding),
                style="bold",
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


def encode_for_output(text: str, encoding: str | None) -> str:
    if not encoding:
        return text
    return text.encode(encoding, errors="backslashreplace").decode(encoding)
