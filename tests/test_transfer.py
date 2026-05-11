import json
import tempfile
import unittest
from pathlib import Path

from codex_sessions_converter.transfer import (
    export_title_slug,
    file_fingerprint,
    format_fingerprint,
    read_rollout_records,
    renamed_rollout_records,
    resolve_export_output_path,
    rollout_filename_date,
    write_rollout_records,
)


class TransferTests(unittest.TestCase):
    def test_renamed_rollout_records_updates_latest_matching_thread_name_event(self) -> None:
        session_id = "11111111-1111-1111-1111-111111111111"
        other_session_id = "22222222-2222-2222-2222-222222222222"
        records = [
            {
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": session_id,
                    "thread_name": "Old title",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": other_session_id,
                    "thread_name": "Other title",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "thread_name_updated",
                    "thread_id": session_id,
                    "thread_name": "Newer old title",
                },
            },
        ]

        updated_records, previous_thread_name, changed = renamed_rollout_records(
            records, session_id, "Replacement title"
        )

        self.assertTrue(changed)
        self.assertEqual(previous_thread_name, "Newer old title")
        self.assertEqual(updated_records[0]["payload"]["thread_name"], "Old title")
        self.assertEqual(updated_records[1]["payload"]["thread_name"], "Other title")
        self.assertEqual(updated_records[2]["payload"]["thread_name"], "Replacement title")

    def test_renamed_rollout_records_inserts_title_event_after_first_record(self) -> None:
        session_id = "11111111-1111-1111-1111-111111111111"
        records = [
            {"timestamp": "2026-04-30T18:20:39Z", "type": "session_meta"},
            {"timestamp": "2026-04-30T18:21:39Z", "type": "response_item"},
        ]

        updated_records, previous_thread_name, changed = renamed_rollout_records(
            records, session_id, "Inserted title"
        )

        self.assertTrue(changed)
        self.assertIsNone(previous_thread_name)
        self.assertEqual(updated_records[0], records[0])
        self.assertEqual(updated_records[1]["type"], "event_msg")
        self.assertEqual(updated_records[1]["timestamp"], "2026-04-30T18:20:39Z")
        self.assertEqual(updated_records[1]["payload"]["type"], "thread_name_updated")
        self.assertEqual(updated_records[1]["payload"]["thread_id"], session_id)
        self.assertEqual(updated_records[1]["payload"]["thread_name"], "Inserted title")
        self.assertEqual(updated_records[2], records[1])

    def test_write_rollout_records_uses_jsonl_readable_by_reader(self) -> None:
        records = [
            {"type": "session_meta", "payload": {"id": "abc"}},
            {"type": "response_item", "payload": {"content": "hello"}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "rollout.jsonl"

            write_rollout_records(output_path, records)

            self.assertEqual(read_rollout_records(output_path), records)
            raw_lines = output_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line) for line in raw_lines], records)

    def test_file_fingerprint_reports_size_and_short_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.jsonl"
            input_path.write_bytes(b"hello")

            fingerprint = file_fingerprint(input_path)

            self.assertEqual(fingerprint.size, 5)
            self.assertEqual(
                format_fingerprint(fingerprint),
                "5 bytes, sha256 2cf24dba5fb0",
            )

    def test_rollout_filename_date_reads_rollout_prefix_date(self) -> None:
        self.assertEqual(
            rollout_filename_date(Path("rollout-2026-04-30T18-20-39-abc.jsonl")),
            ("2026", "04", "30"),
        )
        self.assertIsNone(rollout_filename_date(Path("session.jsonl")))

    def test_export_title_slug_limits_and_sanitizes_title(self) -> None:
        long_title = "Need: clean / portable title " + ("word " * 30)

        self.assertEqual(export_title_slug("!!!"), "session")
        self.assertEqual(
            export_title_slug(long_title),
            "Need-clean-portable-title-word-word-word-word-word-word-word-word-word-word-word",
        )

    def test_resolve_export_output_path_handles_default_file_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "exports"
            output_dir.mkdir()

            self.assertEqual(
                resolve_export_output_path(output_dir, "session.jsonl"),
                output_dir / "session.jsonl",
            )
            self.assertEqual(
                resolve_export_output_path(Path(tmpdir) / "new-dir", "session.jsonl"),
                Path(tmpdir) / "new-dir" / "session.jsonl",
            )
            self.assertEqual(
                resolve_export_output_path(Path(tmpdir) / "named.jsonl", "session.jsonl"),
                Path(tmpdir) / "named.jsonl",
            )


if __name__ == "__main__":
    unittest.main()
