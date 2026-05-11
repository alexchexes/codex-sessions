import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions_converter.session_index import (  # noqa: E402
    SessionIndexEntry,
    SessionIndexError,
    append_session_index_records,
    read_session_index,
    resolve_session_index_record,
    session_index_records,
    write_session_index_records,
)


@dataclass(frozen=True)
class Candidate:
    session_id: str
    thread_name: str
    updated_at: datetime | None


class SessionIndexTests(unittest.TestCase):
    def test_read_session_index_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(read_session_index(Path(tmpdir) / "session_index.jsonl"), [])

    def test_read_session_index_accepts_concatenated_json_and_ignores_invalid_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "session_index.jsonl"
            first_id = "11111111-1111-1111-1111-111111111111"
            second_id = "22222222-2222-2222-2222-222222222222"
            index_path.write_text(
                (
                    json.dumps(
                        {
                            "id": first_id,
                            "thread_name": "First",
                            "updated_at": "2026-04-30T18:21:39Z",
                        }
                    )
                    + json.dumps({"thread_name": "Missing id"})
                    + "\n"
                    + json.dumps({"id": "", "thread_name": "Empty id"})
                    + "\n"
                    + json.dumps({"id": second_id, "thread_name": 123})
                    + "\n"
                    + json.dumps(["not", "a", "record"])
                    + "\n"
                ),
                encoding="utf-8",
            )

            entries = read_session_index(index_path)

            self.assertEqual(
                entries,
                [
                    SessionIndexEntry(
                        session_id=first_id,
                        thread_name="First",
                        updated_at=datetime(2026, 4, 30, 18, 21, 39, tzinfo=timezone.utc),
                    ),
                    SessionIndexEntry(
                        session_id=second_id,
                        thread_name="",
                        updated_at=None,
                    ),
                ],
            )

    def test_append_session_index_records_preserves_existing_text_and_adds_separator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "session_index.jsonl"
            index_path.write_text(
                '{"id":"00000000-0000-0000-0000-000000000000","thread_name":"Existing"}',
                encoding="utf-8",
            )

            append_session_index_records(
                index_path,
                [
                    Candidate(
                        session_id="33333333-3333-3333-3333-333333333333",
                        thread_name="Added",
                        updated_at=datetime(2026, 4, 30, 18, 21, 39, tzinfo=timezone.utc),
                    )
                ],
            )

            self.assertEqual(
                index_path.read_text(encoding="utf-8"),
                (
                    '{"id":"00000000-0000-0000-0000-000000000000","thread_name":"Existing"}\n'
                    '{"id":"33333333-3333-3333-3333-333333333333",'
                    '"thread_name":"Added","updated_at":"2026-04-30T18:21:39Z"}\n'
                ),
            )

    def test_session_index_records_missing_file_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "session_index.jsonl"

            with self.assertRaises(SessionIndexError) as raised:
                session_index_records(index_path)

            self.assertIn("session_index.jsonl not found", str(raised.exception))

    def test_write_session_index_records_writes_compact_jsonl_and_preserves_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "session_index.jsonl"
            index_path.write_text("old\n", encoding="utf-8")

            write_session_index_records(
                index_path,
                [
                    {"id": "44444444-4444-4444-4444-444444444444", "thread_name": "Title"},
                    ["raw", "non-dict"],
                ],
            )

            self.assertEqual(
                index_path.read_text(encoding="utf-8"),
                (
                    '{"id":"44444444-4444-4444-4444-444444444444","thread_name":"Title"}\n'
                    '["raw","non-dict"]\n'
                ),
            )

    def test_resolve_session_index_record_matches_id_and_title(self) -> None:
        records = [
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "thread_name": "By ID",
            },
            {
                "id": "66666666-6666-6666-6666-666666666666",
                "thread_name": "By title",
            },
        ]

        self.assertEqual(
            resolve_session_index_record(records, "55555555-5555-5555-5555-555555555555"),
            (0, records[0]),
        )
        self.assertEqual(resolve_session_index_record(records, "By title"), (1, records[1]))

    def test_resolve_session_index_record_errors_for_missing_and_duplicate_title(
        self,
    ) -> None:
        records = [
            {
                "id": "77777777-7777-7777-7777-777777777777",
                "thread_name": "Duplicate",
            },
            {
                "id": "88888888-8888-8888-8888-888888888888",
                "thread_name": "Duplicate",
            },
        ]

        with self.assertRaises(SessionIndexError) as missing:
            resolve_session_index_record(records, "Missing")
        self.assertIn("No session_index.jsonl entry found for title", str(missing.exception))

        with self.assertRaises(SessionIndexError) as duplicate:
            resolve_session_index_record(records, "Duplicate")
        self.assertIn(
            "Multiple session_index.jsonl entries matched title",
            str(duplicate.exception),
        )


if __name__ == "__main__":
    unittest.main()
