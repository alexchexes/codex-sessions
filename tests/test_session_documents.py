import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_sessions.sessions.documents import (
    SearchDocument,
    build_search_document,
    infer_search_document_title,
    infer_title_from_message,
    inferred_thread_name,
    is_session_activity_record,
    sanitize,
)


class SessionDocumentTests(unittest.TestCase):
    def test_infer_search_document_title_prefers_thread_name(self) -> None:
        document = SearchDocument(
            session_id="11111111-1111-1111-1111-111111111111",
            thread_name="Stored rollout title",
            started_at=None,
            ended_at=None,
            last_activity_at=None,
            visible_lines=("User: User message title",),
            metadata_lines=(),
            tool_input_lines=(),
            tool_output_lines=(),
        )

        self.assertEqual(infer_search_document_title(document), "Stored rollout title")

    def test_infer_search_document_title_prefers_user_line_over_codex_line(self) -> None:
        document = SearchDocument(
            session_id="11111111-1111-1111-1111-111111111111",
            thread_name=None,
            started_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
            ended_at=None,
            last_activity_at=None,
            visible_lines=(
                "Codex: Earlier assistant line",
                "User: Please investigate the import/export behavior.",
            ),
            metadata_lines=(),
            tool_input_lines=(),
            tool_output_lines=(),
        )

        self.assertEqual(
            infer_search_document_title(document),
            "Please investigate the import/export behavior.",
        )

    def test_inferred_thread_name_falls_back_to_session_prefix(self) -> None:
        document = SearchDocument(
            session_id="11111111-2222-3333-4444-555555555555",
            thread_name=None,
            started_at=None,
            ended_at=None,
            last_activity_at=None,
            visible_lines=(),
            metadata_lines=(),
            tool_input_lines=(),
            tool_output_lines=(),
        )

        self.assertEqual(inferred_thread_name(document), "Imported session 11111111")

    def test_infer_title_from_message_compacts_long_text_to_word_boundary(self) -> None:
        self.assertEqual(
            infer_title_from_message("  ## Fix the transfer command. More text"),
            "Fix the transfer command.",
        )
        self.assertEqual(
            infer_title_from_message("word " * 30),
            "word word word word word word word word word word word word",
        )

    def test_sanitize_replaces_encrypted_content_recursively(self) -> None:
        self.assertEqual(
            sanitize(
                {"payload": [{"encrypted_content": "secret"}, {"visible": "kept"}]},
                "...",
            ),
            {"payload": [{"encrypted_content": "..."}, {"visible": "kept"}]},
        )

    def test_session_activity_excludes_only_administrative_records(self) -> None:
        excluded = [
            {"type": "session_meta", "payload": {}},
            {"type": "world_state", "payload": {}},
            {"type": "event_msg", "payload": {"type": "thread_name_updated"}},
        ]
        included = [
            {"type": "turn_context", "payload": {}},
            {"type": "compacted", "payload": {}},
            {"type": "response_item", "payload": {"type": "reasoning"}},
            {"type": "event_msg", "payload": {"type": "token_count"}},
            {"type": "event_msg", "payload": {"type": "turn_aborted"}},
            {"type": "future_rollout_record", "payload": {}},
        ]

        for record in excluded:
            with self.subTest(record=record):
                self.assertFalse(is_session_activity_record(record))
        for record in included:
            with self.subTest(record=record):
                self.assertTrue(is_session_activity_record(record))

    def test_build_search_document_extracts_metadata_and_renders_sanitized_lines(self) -> None:
        session_id = "11111111-1111-1111-1111-111111111111"

        def render_line_groups(record: dict[str, Any]) -> list[tuple[str, list[str]]]:
            payload = record.get("payload")
            if record.get("type") == "session_meta":
                return [("metadata", ["Session metadata: present"])]
            if isinstance(payload, dict) and payload.get("encrypted_content"):
                return [("visible", [f"User: {payload['encrypted_content']}"])]
            if isinstance(payload, dict) and payload.get("type") == "function_call":
                return [("tool_inputs", ["Tool call: shell_command"])]
            if isinstance(payload, dict) and payload.get("type") == "function_call_output":
                return [("tool_outputs", ["Tool output: shell_command: done"])]
            return []

        records = [
            {
                "timestamp": "2026-04-30T18:20:39Z",
                "type": "session_meta",
                "payload": {"id": session_id},
            },
            {
                "timestamp": "2026-04-30T18:20:40Z",
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": session_id,
                    "thread_name": "Rollout title",
                },
            },
            {
                "timestamp": "2026-04-30T18:20:41Z",
                "type": "response_item",
                "payload": {"encrypted_content": "secret"},
            },
            {
                "timestamp": "2026-04-30T18:20:42Z",
                "type": "response_item",
                "payload": {"type": "function_call"},
            },
            {
                "timestamp": "2026-04-30T18:20:43Z",
                "type": "response_item",
                "payload": {"type": "function_call_output"},
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            rollout_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            document = build_search_document(
                rollout_path,
                "...",
                render_line_groups=render_line_groups,
            )

        self.assertEqual(document.session_id, session_id)
        self.assertTrue(document.session_id_is_canonical)
        self.assertEqual(document.thread_name, "Rollout title")
        self.assertEqual(
            document.started_at, datetime(2026, 4, 30, 18, 20, 39, tzinfo=timezone.utc)
        )
        self.assertEqual(document.ended_at, datetime(2026, 4, 30, 18, 20, 43, tzinfo=timezone.utc))
        self.assertEqual(
            document.last_activity_at,
            datetime(2026, 4, 30, 18, 20, 43, tzinfo=timezone.utc),
        )
        self.assertEqual(document.visible_lines, ("User: ...",))
        self.assertEqual(document.metadata_lines, ("Session metadata: present",))
        self.assertEqual(document.tool_input_lines, ("Tool call: shell_command",))
        self.assertEqual(document.tool_output_lines, ("Tool output: shell_command: done",))
        self.assertEqual(
            document.tool_lines,
            ("Tool call: shell_command", "Tool output: shell_command: done"),
        )


if __name__ == "__main__":
    unittest.main()
