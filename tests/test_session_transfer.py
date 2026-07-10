import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_sessions.sessions.documents import SearchDocument
from codex_sessions.sessions.rollout import FileFingerprint, ImportSessionPlan
from codex_sessions.sessions.transfer import (
    build_transfer_document,
    default_export_filename,
    existing_index_record_for_id,
    import_target_path,
    session_index_record_for_import_plan,
    session_index_records_for_import,
)

SESSION_ID = "019de863-c167-7942-9e39-9a3291b9bf55"


def search_document() -> SearchDocument:
    return SearchDocument(
        session_id=SESSION_ID,
        thread_name="Rollout title",
        started_at=datetime(2026, 4, 30, 18, 20, 39, tzinfo=timezone.utc),
        ended_at=datetime(2026, 4, 30, 19, 0, 0, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 4, 30, 19, 0, 0, tzinfo=timezone.utc),
        visible_lines=("User: please transfer this session",),
        metadata_lines=(),
        tool_input_lines=(),
        tool_output_lines=(),
    )


class SessionTransferTests(unittest.TestCase):
    def test_transfer_document_uses_record_one_id_when_title_contains_another_uuid(self) -> None:
        title_id = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / f"2026-04-30--from-{title_id}--{SESSION_ID}.jsonl"
            write_jsonl(
                source_path,
                [{"type": "session_meta", "payload": {"id": SESSION_ID}}],
            )

            document = build_transfer_document(source_path)

        self.assertEqual(document.session_id, SESSION_ID)
        self.assertTrue(document.session_id_is_canonical)
        self.assertIsNone(document.identity_warning)

    def test_transfer_document_ignores_parent_session_meta_in_fork_history(self) -> None:
        parent_id = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / f"rollout-2026-04-30T18-20-39-{SESSION_ID}.jsonl"
            write_jsonl(
                source_path,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": SESSION_ID, "forked_from_id": parent_id},
                    },
                    {"type": "session_meta", "payload": {"id": parent_id}},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": parent_id,
                            "thread_name": "Parent title",
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": SESSION_ID,
                            "thread_name": "Fork title",
                        },
                    },
                ],
            )

            document = build_transfer_document(source_path)

        self.assertEqual(document.session_id, SESSION_ID)
        self.assertEqual(document.thread_name, "Fork title")

    def test_import_target_path_preserves_codex_rollout_name_and_date(self) -> None:
        source_path = Path(f"rollout-2026-04-30T18-20-39-{SESSION_ID}.jsonl")

        target_path = import_target_path(source_path, Path("sessions"), search_document())

        self.assertEqual(target_path, Path("sessions/2026/04/30") / source_path.name)

    def test_import_target_path_generates_codex_rollout_name_for_bare_file(self) -> None:
        target_path = import_target_path(
            Path("friend-copy.jsonl"), Path("sessions"), search_document()
        )

        self.assertEqual(target_path.parent, Path("sessions/2026/04/30"))
        self.assertTrue(target_path.name.startswith("rollout-"))
        self.assertTrue(target_path.name.endswith(f"-{SESSION_ID}.jsonl"))

    def test_import_target_path_replaces_mismatched_rollout_filename_id(self) -> None:
        wrong_id = "11111111-2222-3333-4444-555555555555"
        source_path = Path(f"rollout-2026-04-30T18-20-39-{wrong_id}.jsonl")

        target_path = import_target_path(source_path, Path("sessions"), search_document())

        self.assertNotEqual(target_path.name, source_path.name)
        self.assertTrue(target_path.name.endswith(f"-{SESSION_ID}.jsonl"))

    def test_existing_index_record_for_id_matches_case_insensitively(self) -> None:
        records = [
            {"id": "other", "thread_name": "Other"},
            {"id": SESSION_ID.upper(), "thread_name": "Matched"},
        ]

        match = existing_index_record_for_id(records, SESSION_ID)

        self.assertIsNotNone(match)
        if match is not None:
            self.assertEqual(match[0], 1)
            self.assertEqual(match[1]["thread_name"], "Matched")

    def test_session_index_record_for_import_plan_uses_session_end_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = ImportSessionPlan(
                source_path=Path("source.jsonl"),
                target_path=Path("target.jsonl"),
                session_index_path=Path(tmpdir) / "session_index.jsonl",
                session_id=SESSION_ID,
                thread_name="Imported title",
                started_at=datetime(2026, 4, 30, 18, 20, 39, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 30, 19, 0, 0, tzinfo=timezone.utc),
                index_action="add",
                existing_index_thread_name=None,
                source_fingerprint=FileFingerprint(size=10, sha256="a" * 64),
                rollout_will_be_rewritten=False,
            )

            self.assertEqual(
                session_index_record_for_import_plan(plan),
                {
                    "id": SESSION_ID,
                    "thread_name": "Imported title",
                    "updated_at": "2026-04-30T19:00:00Z",
                },
            )

    def test_session_index_records_for_import_updates_only_matching_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "session_index.jsonl"
            index_path.write_text(
                (
                    '{"id":"other","thread_name":"Other","updated_at":"2026-01-01T00:00:00Z"}\n'
                    f'{{"id":"{SESSION_ID}","thread_name":"Old","updated_at":"2026-01-01T00:00:00Z"}}\n'
                ),
                encoding="utf-8",
            )
            plan = ImportSessionPlan(
                source_path=Path("source.jsonl"),
                target_path=Path("target.jsonl"),
                session_index_path=index_path,
                session_id=SESSION_ID,
                thread_name="Updated",
                started_at=None,
                ended_at=None,
                index_action="update",
                existing_index_thread_name="Old",
                source_fingerprint=FileFingerprint(size=10, sha256="a" * 64),
                rollout_will_be_rewritten=False,
            )

            records = session_index_records_for_import(plan)

            self.assertEqual(records[0]["thread_name"], "Other")
            self.assertEqual(records[1]["thread_name"], "Updated")

    def test_default_export_filename_uses_rollout_date_title_slug_and_id(self) -> None:
        source_path = Path(f"rollout-2026-04-30T18-20-39-{SESSION_ID}.jsonl")

        self.assertEqual(
            default_export_filename(
                source_path, search_document(), SESSION_ID, "Title: with / chars"
            ),
            f"2026-04-30--Title-with-chars--{SESSION_ID}.jsonl",
        )


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
