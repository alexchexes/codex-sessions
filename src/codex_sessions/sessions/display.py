import re
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.text import Text

from codex_sessions.core.terminal import encode_for_output
from codex_sessions.sessions.files import SessionFile
from codex_sessions.sessions.index import SessionIndexEntry, normalize_session_id

NO_ROLLOUT_FILE = "NO ROLLOUT FILE"
NO_SESSION_INDEX_ENTRY = "NO ENTRY IN session_index.jsonl"

SESSION_TIMESTAMP_STYLE = "bright_cyan"
SESSION_TIMESTAMP_DETAIL_STYLE = "cyan"
SESSION_SEPARATOR_STYLE = "bright_black"
SESSION_ID_STYLE = "bright_black"
SESSION_STATUS_STYLE = "bold bright_yellow"


@dataclass(frozen=True)
class SessionDisplayInfo:
    """Structured session row data shared by plain, colored, list, and search output."""

    session_id: str | None
    title: str | None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    relative_path: str | None = None
    status: str | None = None
    identity_status: str | None = None

    @property
    def identifier(self) -> str | None:
        return self.session_id or self.relative_path


def format_local_timestamp(value: datetime | None) -> str:
    if value is None:
        return "UNKNOWN"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def local_timezone_offset_label(value: datetime | None) -> str:
    converted = (value or datetime.now(timezone.utc)).astimezone()
    offset = converted.utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    hours, minutes = divmod(absolute_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def format_timestamp_range(started_at: datetime | None, ended_at: datetime | None) -> str:
    timezone_source = ended_at or started_at
    if started_at is not None and ended_at is not None:
        return (
            f"{format_local_timestamp(started_at)} - "
            f"{format_local_timestamp(ended_at)} "
            f"({local_timezone_offset_label(timezone_source)})"
        )
    if started_at is not None:
        return (
            f"{format_local_timestamp(started_at)} ({local_timezone_offset_label(timezone_source)})"
        )
    if ended_at is not None:
        return (
            f"{format_local_timestamp(ended_at)} ({local_timezone_offset_label(timezone_source)})"
        )
    return ""


def format_session_timestamps(session_file: SessionFile) -> str:
    return format_timestamp_range(session_file.started_at, session_file.ended_at)


def format_session_display_info(info: SessionDisplayInfo) -> str:
    parts = []
    timestamp_text = format_timestamp_range(info.started_at, info.ended_at)
    if timestamp_text:
        parts.append(timestamp_text)
    if info.identifier:
        parts.append(info.identifier)
    if info.title:
        parts.append(info.title)
    if info.status:
        parts.append(info.status)
    if info.identity_status:
        parts.append(info.identity_status)
    return " - ".join(parts)


def indexed_session_display_info(
    entry: SessionIndexEntry,
    session_file: SessionFile | None,
) -> SessionDisplayInfo:
    if session_file is None:
        return SessionDisplayInfo(
            session_id=entry.session_id,
            title=entry.thread_name,
            ended_at=entry.updated_at,
            status=NO_ROLLOUT_FILE,
        )
    return SessionDisplayInfo(
        session_id=entry.session_id,
        title=entry.thread_name,
        started_at=session_file.started_at,
        ended_at=session_file.ended_at or entry.updated_at or session_file.modified_at,
        relative_path=session_file.relative_path,
        identity_status=session_file.identity_status,
    )


def unindexed_session_display_info(
    session_file: SessionFile,
    inferred_title: str | None,
) -> SessionDisplayInfo:
    if not inferred_title:
        return SessionDisplayInfo(
            session_id=None,
            title=None,
            started_at=session_file.started_at,
            ended_at=session_file.ended_at or session_file.modified_at,
            relative_path=session_file.relative_path,
            status=NO_SESSION_INDEX_ENTRY,
            identity_status=session_file.identity_status,
        )
    return SessionDisplayInfo(
        session_id=session_file.session_id,
        title=inferred_title,
        started_at=session_file.started_at,
        ended_at=session_file.ended_at or session_file.modified_at,
        relative_path=session_file.relative_path,
        status=NO_SESSION_INDEX_ENTRY,
        identity_status=session_file.identity_status,
    )


def format_indexed_session_line(entry: SessionIndexEntry, session_file: SessionFile) -> str:
    return format_session_display_info(indexed_session_display_info(entry, session_file))


def format_unindexed_session_line(session_file: SessionFile, inferred_title: str | None) -> str:
    return format_session_display_info(unindexed_session_display_info(session_file, inferred_title))


def session_display_info_for_search(
    session_file: SessionFile,
    entries_by_id: dict[str, SessionIndexEntry],
    inferred_title: str | None = None,
) -> SessionDisplayInfo:
    if session_file.session_id:
        entry = entries_by_id.get(normalize_session_id(session_file.session_id))
        if entry:
            return indexed_session_display_info(entry, session_file)
    return unindexed_session_display_info(session_file, inferred_title)


def session_info_for_search(
    session_file: SessionFile,
    entries_by_id: dict[str, SessionIndexEntry],
    inferred_title: str | None = None,
) -> str:
    return format_session_display_info(
        session_display_info_for_search(session_file, entries_by_id, inferred_title)
    )


def session_title_for_search(
    session_file: SessionFile,
    entries_by_id: dict[str, SessionIndexEntry],
    inferred_title: str | None = None,
) -> str | None:
    return session_display_info_for_search(session_file, entries_by_id, inferred_title).title


def session_title_match_spans(
    info: SessionDisplayInfo,
    search_pattern: re.Pattern[str],
) -> tuple[tuple[int, int], ...]:
    if not info.title:
        return ()
    return tuple(
        match.span()
        for match in search_pattern.finditer(info.title)
        if match.start() != match.end()
    )


def append_encoded(text: Text, value: str, encoding: str | None, *, style: str = "") -> None:
    text.append(encode_for_output(value, encoding), style=style)


def append_session_separator(text: Text, encoding: str | None) -> None:
    append_encoded(text, " - ", encoding, style=SESSION_SEPARATOR_STYLE)


def append_timestamp_range(text: Text, info: SessionDisplayInfo, encoding: str | None) -> bool:
    timezone_source = info.ended_at or info.started_at
    if info.started_at is None and info.ended_at is None:
        return False
    if info.started_at is not None:
        append_encoded(
            text,
            format_local_timestamp(info.started_at),
            encoding,
            style=SESSION_TIMESTAMP_STYLE,
        )
    if info.started_at is not None and info.ended_at is not None:
        append_encoded(text, " - ", encoding, style=SESSION_TIMESTAMP_DETAIL_STYLE)
    if info.ended_at is not None:
        append_encoded(
            text,
            format_local_timestamp(info.ended_at),
            encoding,
            style=SESSION_TIMESTAMP_STYLE,
        )
    append_encoded(
        text,
        f" ({local_timezone_offset_label(timezone_source)})",
        encoding,
        style=SESSION_TIMESTAMP_DETAIL_STYLE,
    )
    return True


def title_text(
    title: str,
    encoding: str | None,
    *,
    title_style: str,
    match_spans: tuple[tuple[int, int], ...],
) -> Text:
    rendered = Text(encode_for_output(title, encoding), style=title_style)
    for start, end in match_spans:
        rendered.stylize("bold bright_red", start, end)
    return rendered


def styled_session_display_text(
    info: SessionDisplayInfo,
    encoding: str | None,
    *,
    title_style: str = "",
    id_style: str = SESSION_ID_STYLE,
    title_matches: tuple[tuple[int, int], ...] = (),
) -> Text:
    rendered = Text()
    has_timestamp = append_timestamp_range(rendered, info, encoding)
    if info.identifier:
        if has_timestamp:
            append_session_separator(rendered, encoding)
        append_encoded(rendered, info.identifier, encoding, style=id_style)
    if info.title:
        if rendered:
            append_session_separator(rendered, encoding)
        rendered.append_text(
            title_text(
                info.title,
                encoding,
                title_style=title_style,
                match_spans=title_matches,
            )
        )
    if info.status:
        if rendered:
            append_session_separator(rendered, encoding)
        append_encoded(rendered, info.status, encoding, style=SESSION_STATUS_STYLE)
    if info.identity_status:
        if rendered:
            append_session_separator(rendered, encoding)
        append_encoded(rendered, info.identity_status, encoding, style=SESSION_STATUS_STYLE)
    return rendered
