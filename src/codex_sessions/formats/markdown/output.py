from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.core.timestamps import parse_timestamp
from codex_sessions.formats.markdown.formatting import (
    fenced_block,
    render_json_block_content,
    render_markdown_table,
)
from codex_sessions.formats.markdown.images import MarkdownImageHandler
from codex_sessions.formats.markdown.message_content import content_to_text
from codex_sessions.formats.markdown.timing import (
    DEFAULT_GAP_THRESHOLD_SECONDS,
    DEFAULT_TOOL_DURATION_THRESHOLD_SECONDS,
    event_duration_seconds,
    format_duration,
    format_markdown_timestamp,
)
from codex_sessions.formats.markdown.tools import (
    render_tool_call,
    render_tool_output,
    tool_display_name,
    tool_name_is_included,
    tool_output_display_name,
)
from codex_sessions.sessions.documents import sanitize
from codex_sessions.sessions.message_content import is_injected_user_context

TOOL_CALL_PAYLOAD_TYPES = {"function_call", "tool_search_call", "custom_tool_call"}
TOOL_OUTPUT_PAYLOAD_TYPES = {
    "function_call_output",
    "tool_search_output",
    "custom_tool_call_output",
}


@dataclass(frozen=True)
class MarkdownOptions:
    tool_mode: str
    tool_preview_chars: int
    include_metadata: bool
    include_raw: bool
    redaction: str
    image_mode: str = "truncate"
    timestamps: bool = False
    gap_threshold_seconds: float = DEFAULT_GAP_THRESHOLD_SECONDS
    tool_duration_threshold_seconds: float = DEFAULT_TOOL_DURATION_THRESHOLD_SECONDS
    include_timing_markers: bool = True
    tool_include: frozenset[str] | None = None


@dataclass(frozen=True)
class MarkdownTiming:
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    tool_durations_by_call_id: dict[str, float]


def render_reasoning(
    payload: dict[str, Any],
    redaction: str,
    image_handler: MarkdownImageHandler | None = None,
) -> str:
    if (
        not payload.get("summary")
        and not payload.get("content")
        and payload.get("encrypted_content") is not None
    ):
        return f"**Reasoning (encrypted_content) {redaction}**"

    lines = ["**Reasoning**"]

    summary = payload.get("summary")
    if summary:
        lines.extend(["", "Summary:"])
        if isinstance(summary, list):
            for item in summary:
                text = (
                    content_to_text(item.get("content"), image_handler)
                    if isinstance(item, dict)
                    else str(item)
                )
                if text:
                    lines.append(f"- {text}")
        else:
            lines.append(str(summary))

    content = payload.get("content")
    if content:
        lines.extend(["", content_to_text(content, image_handler)])
    elif payload.get("encrypted_content") is not None:
        lines.extend(["", f"`encrypted_content`: {redaction}"])

    return "\n".join(lines)


def metadata_title(record: dict[str, Any]) -> str:
    record_type = record.get("type", "metadata")
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type"):
        return f"Metadata: `{record_type}.{payload['type']}`"
    return f"Metadata: `{record_type}`"


def render_metadata(
    record: dict[str, Any],
    image_handler: MarkdownImageHandler | None = None,
) -> str:
    rendered_record = image_handler.transform_value(record) if image_handler else record
    return "\n".join(
        [
            f"Timestamp: `{record.get('timestamp', '')}`",
            "",
            render_markdown_table(rendered_record),
        ]
    )


def render_raw_record(
    line_number: int,
    record: dict[str, Any],
    image_handler: MarkdownImageHandler | None = None,
) -> str:
    rendered_record = image_handler.transform_value(record) if image_handler else record
    return "\n".join(
        [
            f"Line: `{line_number}`",
            "",
            fenced_block(render_json_block_content(rendered_record), "json"),
        ]
    )


def is_metadata_record(record: dict[str, Any]) -> bool:
    record_type = record.get("type")
    if record_type in {"session_meta", "turn_context"}:
        return True
    if record_type == "event_msg":
        payload = record.get("payload")
        if isinstance(payload, dict):
            return payload.get("type") in {
                "mcp_tool_call_end",
                "task_complete",
                "task_started",
                "thread_name_updated",
                "token_count",
            }
    return False


def write_markdown_section(
    dst: TextIO,
    title: str,
    body: str,
    timestamp: datetime | None = None,
    *,
    include_timestamp: bool = False,
) -> None:
    if include_timestamp and timestamp is not None:
        title = f"{title} | {format_markdown_timestamp(timestamp)}"
    dst.write(f"# {title}:\n\n")
    dst.write(body.rstrip())
    dst.write("\n\n---\n\n")


def collect_markdown_timing(input_path: Path) -> MarkdownTiming:
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    tool_durations_by_call_id: dict[str, float] = {}

    for _, record in iter_jsonl_objects(input_path, ignore_invalid_final_line=True):
        record_timestamp = parse_timestamp(record.get("timestamp"))
        if record_timestamp is not None:
            if first_timestamp is None:
                first_timestamp = record_timestamp
            last_timestamp = record_timestamp

        payload = record.get("payload")
        if record.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        call_id = payload.get("call_id")
        duration_seconds = event_duration_seconds(payload)
        if isinstance(call_id, str) and call_id and duration_seconds is not None:
            tool_durations_by_call_id[call_id] = duration_seconds

    return MarkdownTiming(
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        tool_durations_by_call_id=tool_durations_by_call_id,
    )


def rendered_tool_duration(
    payload: dict[str, Any],
    output_timestamp: datetime | None,
    *,
    timing: MarkdownTiming,
    tool_started_at_by_call_id: dict[str, datetime],
    threshold_seconds: float,
) -> float | None:
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None

    duration_seconds = timing.tool_durations_by_call_id.get(call_id)
    if duration_seconds is None and output_timestamp is not None:
        started_at = tool_started_at_by_call_id.get(call_id)
        if started_at is not None:
            elapsed = (output_timestamp - started_at).total_seconds()
            if elapsed >= 0:
                duration_seconds = elapsed

    if duration_seconds is None or duration_seconds < threshold_seconds:
        return None
    return duration_seconds


def convert_jsonl_to_markdown(input_path: Path, output_path: Path, options: MarkdownOptions) -> int:
    count = 0
    seen_dialogue: set[tuple[str, str]] = set()
    tool_names_by_call_id: dict[str, str] = {}
    tool_started_at_by_call_id: dict[str, datetime] = {}
    image_handler = MarkdownImageHandler(options.image_mode, output_path, input_path)
    timing = collect_markdown_timing(input_path)
    include_boundary_markers = options.include_timing_markers and not options.timestamps
    last_timing_timestamp: datetime | None = None

    def write_timing_section(dst: TextIO, title: str, body: str, timestamp: datetime) -> None:
        nonlocal count
        write_markdown_section(
            dst,
            title,
            body,
            timestamp,
            include_timestamp=options.timestamps,
        )
        count += 1

    def maybe_write_gap_marker(dst: TextIO, timestamp: datetime | None) -> None:
        nonlocal last_timing_timestamp
        if not options.include_timing_markers or timestamp is None:
            return
        if last_timing_timestamp is not None:
            gap_seconds = (timestamp - last_timing_timestamp).total_seconds()
            if gap_seconds > 0 and gap_seconds >= options.gap_threshold_seconds:
                write_timing_section(
                    dst,
                    "Time Gap",
                    f"`{format_duration(gap_seconds)}` elapsed since previous rendered event.",
                    timestamp,
                )
        last_timing_timestamp = timestamp

    def write_dialogue(
        dst: TextIO,
        title: str,
        body: str,
        timestamp: datetime | None,
    ) -> None:
        nonlocal count
        normalized_body = body.strip()
        if not normalized_body:
            return
        # Newer rollouts can contain the same visible message in response and event records.
        key = (title, normalized_body)
        if key in seen_dialogue:
            return
        seen_dialogue.add(key)
        maybe_write_gap_marker(dst, timestamp)
        write_markdown_section(
            dst,
            title,
            normalized_body,
            timestamp,
            include_timestamp=options.timestamps,
        )
        count += 1

    def write_section(
        dst: TextIO,
        title: str,
        body: str,
        timestamp: datetime | None,
    ) -> None:
        nonlocal count
        normalized_body = body.strip()
        if not normalized_body:
            return
        maybe_write_gap_marker(dst, timestamp)
        write_markdown_section(
            dst,
            title,
            normalized_body,
            timestamp,
            include_timestamp=options.timestamps,
        )
        count += 1

    with output_path.open("w", encoding="utf-8", newline="\n") as dst:
        if include_boundary_markers and timing.first_timestamp is not None:
            write_timing_section(
                dst,
                "First Record",
                format_markdown_timestamp(timing.first_timestamp),
                timing.first_timestamp,
            )
            last_timing_timestamp = timing.first_timestamp

        for line_number, raw_record in iter_jsonl_objects(
            input_path, ignore_invalid_final_line=True
        ):
            image_handler.set_source_line(line_number)
            record = sanitize(raw_record, options.redaction)
            record_timestamp = parse_timestamp(raw_record.get("timestamp"))
            record_type = record.get("type")
            payload = record.get("payload")
            handled = False

            if record_type == "response_item" and isinstance(payload, dict):
                payload_type = payload.get("type")
                if payload_type == "message":
                    role = payload.get("role")
                    text = content_to_text(payload.get("content"), image_handler)
                    if role == "assistant":
                        write_dialogue(dst, "Codex", text, record_timestamp)
                        handled = True
                    elif role == "user" and not is_injected_user_context(text):
                        write_dialogue(dst, "User", text, record_timestamp)
                        handled = True
                elif payload_type == "reasoning":
                    write_section(
                        dst,
                        "Codex",
                        render_reasoning(payload, options.redaction, image_handler),
                        record_timestamp,
                    )
                    handled = True
                elif payload_type in TOOL_CALL_PAYLOAD_TYPES:
                    call_id = payload.get("call_id")
                    tool_name = tool_display_name(payload)
                    if isinstance(call_id, str) and call_id and record_timestamp is not None:
                        tool_started_at_by_call_id[call_id] = record_timestamp
                    if isinstance(call_id, str) and call_id:
                        tool_names_by_call_id[call_id] = tool_name
                    if options.tool_mode != "none" and tool_name_is_included(
                        tool_name, options.tool_include
                    ):
                        rendered_tool_call, _ = render_tool_call(
                            payload,
                            options.tool_mode,
                            options.tool_preview_chars,
                            image_handler,
                        )
                        write_section(dst, "Codex", rendered_tool_call, record_timestamp)
                    handled = True
                elif payload_type in TOOL_OUTPUT_PAYLOAD_TYPES:
                    tool_name = tool_output_display_name(payload, tool_names_by_call_id)
                    if options.tool_mode != "none" and tool_name_is_included(
                        tool_name, options.tool_include
                    ):
                        write_section(
                            dst,
                            "Codex",
                            render_tool_output(
                                payload,
                                options.tool_mode,
                                options.tool_preview_chars,
                                tool_names_by_call_id,
                                image_handler,
                                duration_seconds=rendered_tool_duration(
                                    payload,
                                    record_timestamp,
                                    timing=timing,
                                    tool_started_at_by_call_id=tool_started_at_by_call_id,
                                    threshold_seconds=options.tool_duration_threshold_seconds,
                                ),
                            ),
                            record_timestamp,
                        )
                    handled = True

            elif record_type == "event_msg" and isinstance(payload, dict):
                payload_type = payload.get("type")
                if payload_type == "user_message":
                    write_dialogue(dst, "User", payload.get("message", ""), record_timestamp)
                    handled = True
                elif payload_type == "agent_message":
                    write_dialogue(dst, "Codex", payload.get("message", ""), record_timestamp)
                    handled = True

            if not handled and options.include_metadata and is_metadata_record(record):
                write_section(
                    dst,
                    metadata_title(record),
                    render_metadata(record, image_handler),
                    record_timestamp,
                )
                handled = True

            if not handled and options.include_raw:
                write_section(
                    dst,
                    "Raw",
                    render_raw_record(line_number, record, image_handler),
                    record_timestamp,
                )

        if include_boundary_markers and timing.last_timestamp is not None:
            maybe_write_gap_marker(dst, timing.last_timestamp)
            write_timing_section(
                dst,
                "Latest Record",
                format_markdown_timestamp(timing.last_timestamp),
                timing.last_timestamp,
            )

    return count
