import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.sessions.files import SessionIdentity, resolve_session_identity
from codex_sessions.sessions.rollout import (
    thread_name_updated_matches_session,
    thread_name_updated_name,
)

LineGroupRenderer = Callable[[dict[str, Any]], Iterable[tuple[str, Sequence[str]]]]
MAX_INFERRED_TITLE_CHARS = 80
MAX_INFERRED_TITLE_WORDS = 12
ADMINISTRATIVE_RECORD_TYPES = {"session_meta", "world_state"}
ADMINISTRATIVE_EVENT_TYPES = {"thread_name_updated"}


@dataclass(frozen=True)
class SearchDocument:
    session_id: str | None
    thread_name: str | None
    started_at: datetime | None
    ended_at: datetime | None
    last_activity_at: datetime | None
    visible_lines: tuple[str, ...]
    metadata_lines: tuple[str, ...]
    tool_input_lines: tuple[str, ...]
    tool_output_lines: tuple[str, ...]
    session_id_is_canonical: bool = False
    identity_warning: str | None = None
    identity_status: str | None = None

    @property
    def tool_lines(self) -> tuple[str, ...]:
        return (*self.tool_input_lines, *self.tool_output_lines)


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


def is_session_activity_record(record: dict[str, Any]) -> bool:
    """Return whether a timestamped rollout record represents session activity."""
    record_type = record.get("type")
    if isinstance(record_type, str) and record_type in ADMINISTRATIVE_RECORD_TYPES:
        return False

    payload = record.get("payload")
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    return not (
        record_type == "event_msg"
        and isinstance(payload_type, str)
        and payload_type in ADMINISTRATIVE_EVENT_TYPES
    )


def build_search_document(
    input_path: Path,
    redaction: str,
    *,
    render_line_groups: LineGroupRenderer,
) -> SearchDocument:
    """Extract one compact, de-duplicated searchable document from a rollout file."""
    identity: SessionIdentity | None = None
    session_id: str | None = None
    thread_name: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_activity_at: datetime | None = None
    line_groups: dict[str, list[str]] = {
        "visible": [],
        "metadata": [],
        "tool_inputs": [],
        "tool_outputs": [],
    }
    seen_lines: dict[str, set[str]] = {
        "visible": set(),
        "metadata": set(),
        "tool_inputs": set(),
        "tool_outputs": set(),
    }

    for _, raw_record in iter_jsonl_objects(input_path):
        if identity is None:
            identity = resolve_session_identity(input_path, raw_record)
            session_id = identity.session_id
        record_timestamp = parse_timestamp(raw_record.get("timestamp"))
        if record_timestamp is not None:
            ended_at = record_timestamp
            if is_session_activity_record(raw_record):
                last_activity_at = record_timestamp

        payload = raw_record.get("payload")
        if started_at is None:
            started_at = record_timestamp
            if started_at is None and isinstance(payload, dict):
                started_at = parse_timestamp(payload.get("timestamp"))
        if raw_record.get("type") == "event_msg" and isinstance(payload, dict):
            if thread_name_updated_matches_session(payload, session_id):
                event_thread_name = thread_name_updated_name(payload)
                if event_thread_name:
                    thread_name = event_thread_name

        record = sanitize(raw_record, redaction)
        for group, lines in render_line_groups(record):
            normalized_group = "tool_inputs" if group == "tools" else group
            if normalized_group not in line_groups:
                continue
            for line in lines:
                if line and line not in seen_lines[normalized_group]:
                    seen_lines[normalized_group].add(line)
                    line_groups[normalized_group].append(line)

    if identity is None:
        identity = resolve_session_identity(input_path, None)
        session_id = identity.session_id

    return SearchDocument(
        session_id=session_id,
        thread_name=thread_name,
        started_at=started_at,
        ended_at=ended_at,
        last_activity_at=last_activity_at,
        visible_lines=tuple(line_groups["visible"]),
        metadata_lines=tuple(line_groups["metadata"]),
        tool_input_lines=tuple(line_groups["tool_inputs"]),
        tool_output_lines=tuple(line_groups["tool_outputs"]),
        session_id_is_canonical=identity.is_canonical,
        identity_warning=identity.warning,
        identity_status=identity.status,
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

    # Prefer a complete first sentence when it is short enough to be readable as a title.
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
