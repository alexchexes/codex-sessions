import os
from collections.abc import Mapping
from typing import TextIO

from rich.console import Console


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


def terminal_console(stream: TextIO, *, color: str = "auto") -> Console:
    force_terminal, no_color = console_color_options(color, stream)
    return Console(
        file=stream,
        force_terminal=force_terminal,
        no_color=no_color,
        highlight=False,
        legacy_windows=False,
    )


def encode_for_output(text: str, encoding: str | None) -> str:
    if not encoding:
        return text
    return text.encode(encoding, errors="backslashreplace").decode(encoding)
