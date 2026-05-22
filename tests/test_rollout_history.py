import json
import tempfile
import unittest
from pathlib import Path

from codex_sessions.sessions.rollout_history import (
    RolloutHistoryRelation,
    compare_rollout_histories,
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_SESSION_ID = "66666666-7777-8888-9999-000000000000"


class RolloutHistoryTests(unittest.TestCase):
    def test_identical_rollouts_use_fingerprint_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            records = base_records()
            write_jsonl(local_path, records)
            write_jsonl(incoming_path, records)

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.IDENTICAL)
        self.assertIsNone(comparison.common_comparable_records)
        self.assertEqual(comparison.local_tail_comparable_records, 0)
        self.assertEqual(comparison.incoming_tail_comparable_records, 0)

    def test_title_events_do_not_change_comparable_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, [title_record("Local title"), *base_records()])
            write_jsonl(incoming_path, [*base_records(), title_record("Incoming title")])

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.EQUIVALENT)
        self.assertEqual(comparison.common_comparable_records, 2)
        self.assertEqual(comparison.local_tail_comparable_records, 0)
        self.assertEqual(comparison.incoming_tail_comparable_records, 0)

    def test_incoming_ahead_when_local_comparable_history_is_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, base_records())
            write_jsonl(incoming_path, [*base_records(), message_record("incoming tail")])

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.INCOMING_AHEAD)
        self.assertEqual(comparison.common_comparable_records, 2)
        self.assertEqual(comparison.local_tail_comparable_records, 0)
        self.assertEqual(comparison.incoming_tail_comparable_records, 1)

    def test_local_ahead_when_incoming_comparable_history_is_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, [*base_records(), message_record("local tail")])
            write_jsonl(incoming_path, base_records())

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.LOCAL_AHEAD)
        self.assertEqual(comparison.common_comparable_records, 2)
        self.assertEqual(comparison.local_tail_comparable_records, 1)
        self.assertEqual(comparison.incoming_tail_comparable_records, 0)

    def test_histories_diverge_after_common_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, [*base_records(), message_record("local tail")])
            write_jsonl(incoming_path, [*base_records(), message_record("incoming tail")])

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.DIVERGED)
        self.assertEqual(comparison.common_comparable_records, 2)
        self.assertEqual(comparison.local_tail_comparable_records, 1)
        self.assertEqual(comparison.incoming_tail_comparable_records, 1)

    def test_changed_non_title_record_shape_diverges_even_with_same_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            local_message = message_record("same visible message")
            incoming_message = message_record("same visible message")
            incoming_payload = incoming_message["payload"]
            if not isinstance(incoming_payload, dict):
                self.fail("message payload must be a dictionary")
            incoming_payload["new_shape"] = True
            write_jsonl(local_path, [base_records()[0], local_message])
            write_jsonl(incoming_path, [base_records()[0], incoming_message])

            comparison = compare_rollout_histories(
                local_path,
                incoming_path,
                local_session_id=SESSION_ID,
                incoming_session_id=SESSION_ID,
            )

        self.assertEqual(comparison.relation, RolloutHistoryRelation.DIVERGED)
        self.assertEqual(comparison.common_comparable_records, 1)
        self.assertEqual(comparison.local_tail_comparable_records, 1)
        self.assertEqual(comparison.incoming_tail_comparable_records, 1)

    def test_comparison_refuses_different_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, base_records())
            write_jsonl(incoming_path, base_records())

            with self.assertRaisesRegex(ValueError, "different session IDs"):
                compare_rollout_histories(
                    local_path,
                    incoming_path,
                    local_session_id=SESSION_ID,
                    incoming_session_id=OTHER_SESSION_ID,
                )

    def test_comparison_reports_invalid_json_from_history_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path, incoming_path = rollout_paths(Path(tmpdir))
            write_jsonl(local_path, base_records())
            incoming_path.write_text('{"type":"session_meta"}\nnot-json\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                compare_rollout_histories(
                    local_path,
                    incoming_path,
                    local_session_id=SESSION_ID,
                    incoming_session_id=SESSION_ID,
                )


def rollout_paths(root: Path) -> tuple[Path, Path]:
    return root / "local.jsonl", root / "incoming.jsonl"


def base_records() -> list[dict[str, object]]:
    return [
        {"type": "session_meta", "payload": {"id": SESSION_ID}},
        message_record("common message"),
    ]


def title_record(title: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "payload": {
            "type": "thread_name_updated",
            "thread_id": SESSION_ID,
            "thread_name": title,
        },
    }


def message_record(content: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": content},
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
