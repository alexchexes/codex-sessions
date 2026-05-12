import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.sessions.rollout import (
    thread_name_updated_matches_session,
    thread_name_updated_name,
    thread_name_updated_session_id,
)

LineGroupRenderer = Callable[[dict[str, Any]], Iterable[tuple[str, Sequence[str]]]]
SessionIdFromPath = Callable[[Path], str | None]
MAX_INFERRED_TITLE_CHARS = 80
MAX_INFERRED_TITLE_WORDS = 12


@dataclass(frozen=True)
class SearchDocument:
    session_id: str | None
    thread_name: str | None
    started_at: datetime | None
    ended_at: datetime | None
    visible_lines: tuple[str, ...]
    metadata_lines: tuple[str, ...]
    tool_lines: tuple[str, ...]


def sanitize(value: Any, redaction: str) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, inner in value.items():
            if key == "encrypted_content":
                sanitized[key] = redaction
            else:
                sanitized[key] = sanitize(inner, redaction)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item, redaction) for item in value]
    return value


def build_search_document(
    input_path: Path,
    redaction: str,
    *,
    session_id_from_path: SessionIdFromPath,
    render_line_groups: LineGroupRenderer,
) -> SearchDocument:
    session_id = session_id_from_path(input_path)
    thread_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    line_groups: dict[str, list[str]] = {"visible": [], "metadata": [], "tools": []}
    seen_lines: dict[str, set[str]] = {"visible": set(), "metadata": set(), "tools": set()}

    for _, raw_record in iter_jsonl_objects(input_path):
        record_timestamp = parse_timestamp(raw_record.get("timestamp"))
        if record_timestamp is not None:
            ended_at = record_timestamp

        payload = raw_record.get("payload")
        if started_at is None:
            started_at = record_timestamp
            if started_at is None and isinstance(payload, dict):
                started_at = parse_timestamp(payload.get("timestamp"))
        if (
            session_id is None
            and raw_record.get("type") == "session_meta"
            and isinstance(payload, dict)
        ):
            payload_id = payload.get("id")
            if isinstance(payload_id, str) and payload_id:
                session_id = payload_id
        if raw_record.get("type") == "event_msg" and isinstance(payload, dict):
            event_session_id = thread_name_updated_session_id(payload)
            if session_id is None and event_session_id:
                session_id = event_session_id
            if thread_name_updated_matches_session(payload, session_id):
                event_thread_name = thread_name_updated_name(payload)
                if event_thread_name:
                    thread_name = event_thread_name

        record = sanitize(raw_record, redaction)
        for group, lines in render_line_groups(record):
            for line in lines:
                if line and line not in seen_lines[group]:
                    seen_lines[group].add(line)
                    line_groups[group].append(line)

    return SearchDocument(
        session_id=session_id,
        thread_name=thread_name,
        started_at=started_at,
        ended_at=ended_at,
        visible_lines=tuple(line_groups["visible"]),
        metadata_lines=tuple(line_groups["metadata"]),
        tool_lines=tuple(line_groups["tools"]),
    )


def infer_search_document_title(document: SearchDocument) -> str | None:
    if document.thread_name:
        return document.thread_name
    user_title = first_inferred_title_with_prefix(document.visible_lines, "User: ")
    if user_title:
        return user_title
    return first_inferred_title_with_prefix(document.visible_lines, "Codex: ")


def fallback_thread_name(session_id: str) -> str:
    return f"Imported session {session_id[:8]}"


def inferred_thread_name(document: SearchDocument) -> str:
    if document.session_id is None:
        return "Imported session"
    return infer_search_document_title(document) or fallback_thread_name(document.session_id)


def first_inferred_title_with_prefix(lines: Sequence[str], prefix: str) -> str | None:
    for line in lines:
        if not line.startswith(prefix):
            continue
        title = infer_title_from_message(line[len(prefix) :])
        if title:
            return title
    return None


def infer_title_from_message(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip(" \t\r\n#*-_`\"'")
    if not normalized:
        return None

    sentence_match = re.search(r"(?<=[.!?])\s+", normalized)
    if sentence_match:
        sentence = normalized[: sentence_match.start() + 1].strip()
        if 8 <= len(sentence) <= MAX_INFERRED_TITLE_CHARS:
            return sentence

    if len(normalized) <= MAX_INFERRED_TITLE_CHARS:
        return normalized

    words = normalized.split()
    selected_words: list[str] = []
    selected_length = 0
    for word in words[:MAX_INFERRED_TITLE_WORDS]:
        next_length = selected_length + len(word) + (1 if selected_words else 0)
        if next_length > MAX_INFERRED_TITLE_CHARS:
            break
        selected_words.append(word)
        selected_length = next_length

    if selected_words:
        return " ".join(selected_words)
    return normalized[:MAX_INFERRED_TITLE_CHARS].rstrip()
