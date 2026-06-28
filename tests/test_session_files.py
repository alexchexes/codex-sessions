import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_sessions.sessions.files import (
    FILENAME_ID_MISMATCH_STATUS,
    INVALID_SESSION_META_FILENAME_ID_STATUS,
    INVALID_SESSION_META_NO_ID_STATUS,
    discover_session_files,
    format_session_file_path,
    read_session_identity,
    session_file_metadata,
    session_id_from_metadata,
    session_id_from_path,
)


class SessionFileTests(unittest.TestCase):
    def test_session_id_from_path_reads_uuid_from_rollout_name(self) -> None:
        session_id = "11111111-2222-3333-4444-555555555555"
        title_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        self.assertEqual(
            session_id_from_path(Path(f"rollout-2026-04-30T18-20-39-{session_id}.jsonl")),
            session_id,
        )
        self.assertEqual(
            session_id_from_path(Path(f"2026-04-30--from-{title_id}--{session_id}.jsonl")),
            session_id,
        )
        self.assertIsNone(session_id_from_path(Path(f"2026-04-30--from-{title_id}--notes.jsonl")))
        self.assertIsNone(session_id_from_path(Path("rollout-without-id.jsonl")))

    def test_session_id_from_metadata_requires_record_one_session_meta(self) -> None:
        session_id = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(
                rollout_path,
                [
                    {"type": "response_item", "payload": {"content": "before"}},
                    {"type": "session_meta", "payload": {"id": session_id}},
                ],
            )

            self.assertIsNone(session_id_from_metadata(rollout_path))

            write_jsonl(
                rollout_path,
                [
                    {"type": "session_meta", "payload": {"id": session_id}},
                    {
                        "type": "session_meta",
                        "payload": {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                    },
                ],
            )

            self.assertEqual(session_id_from_metadata(rollout_path), session_id)

    def test_read_session_identity_prefers_metadata_and_reports_mismatch(self) -> None:
        metadata_id = "11111111-2222-3333-4444-555555555555"
        filename_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / f"rollout-2026-04-30T18-20-39-{filename_id}.jsonl"
            write_jsonl(
                rollout_path,
                [{"type": "session_meta", "payload": {"id": metadata_id}}],
            )

            identity = read_session_identity(rollout_path)

        self.assertEqual(identity.session_id, metadata_id)
        self.assertTrue(identity.is_canonical)
        self.assertEqual(identity.status, FILENAME_ID_MISMATCH_STATUS)
        self.assertIn(filename_id, identity.warning or "")
        self.assertIn(metadata_id, identity.warning or "")

    def test_read_session_identity_falls_back_only_to_trailing_filename_id(self) -> None:
        title_id = "11111111-2222-3333-4444-555555555555"
        filename_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / f"2026-04-30--from-{title_id}--{filename_id}.jsonl"
            write_jsonl(
                rollout_path,
                [
                    {"type": "response_item", "payload": {"content": "before"}},
                    {"type": "session_meta", "payload": {"id": title_id}},
                ],
            )

            identity = read_session_identity(rollout_path)

        self.assertEqual(identity.session_id, filename_id)
        self.assertFalse(identity.is_canonical)
        self.assertEqual(identity.status, INVALID_SESSION_META_FILENAME_ID_STATUS)

    def test_read_session_identity_reports_invalid_metadata_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout-without-id.jsonl"
            write_jsonl(
                rollout_path,
                [{"type": "response_item", "payload": {"content": "before"}}],
            )

            identity = read_session_identity(rollout_path)

        self.assertIsNone(identity.session_id)
        self.assertFalse(identity.is_canonical)
        self.assertEqual(identity.status, INVALID_SESSION_META_NO_ID_STATUS)

    def test_session_file_metadata_reads_id_and_timestamps(self) -> None:
        session_id = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                    },
                ],
            )

            metadata = session_file_metadata(rollout_path, include_ended_at=True)

        self.assertEqual(metadata[0], session_id)
        self.assertEqual(metadata[1], datetime(2026, 4, 30, 18, 20, 39, tzinfo=timezone.utc))
        self.assertEqual(metadata[2], datetime(2026, 4, 30, 18, 21, 39, tzinfo=timezone.utc))

    def test_discover_session_files_returns_sorted_relative_entries(self) -> None:
        first_id = "11111111-1111-1111-1111-111111111111"
        second_id = "22222222-2222-2222-2222-222222222222"
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir) / "sessions"
            second_path = (
                sessions_dir
                / "2026"
                / "05"
                / "02"
                / f"rollout-2026-05-02T12-00-00-{second_id}.jsonl"
            )
            first_path = (
                sessions_dir
                / "2026"
                / "04"
                / "30"
                / f"rollout-2026-04-30T18-20-39-{first_id}.jsonl"
            )
            write_jsonl(second_path, [{"timestamp": "2026-05-02T12:00:00Z"}])
            write_jsonl(first_path, [{"timestamp": "2026-04-30T18:20:39Z"}])

            session_files = discover_session_files(sessions_dir)

        self.assertEqual(
            [session_file.session_id for session_file in session_files], [first_id, second_id]
        )
        self.assertEqual(session_files[0].relative_path, f"2026/04/30/{first_path.name}")
        self.assertEqual(
            format_session_file_path(first_path, sessions_dir), f"2026/04/30/{first_path.name}"
        )


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
