import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402
from codex_sessions.sessions.files import session_id_from_path  # noqa: E402


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    session_id = session_id_from_path(path)
    if session_id and (not records or records[0].get("type") != "session_meta"):
        records = [{"type": "session_meta", "payload": {"id": session_id}}, *records]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def import_title_record(session_id: str, thread_name: str, timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "thread_name_updated",
            "thread_id": session_id,
            "thread_name": thread_name,
        },
    }


def import_user_message(content: str, timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": content,
        },
    }


class CliImportMergeTests(unittest.TestCase):
    def test_import_merge_fast_forwards_existing_rollout_and_reports_title_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "54545454-5454-5454-5454-545454545454"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {
                        "id": session_id,
                        "thread_name": "Local merge title",
                        "updated_at": "2026-04-30T18:21:39Z",
                        "extra": "preserved",
                    }
                ],
            )
            local_records = [
                {
                    "timestamp": "2026-04-30T18:20:40Z",
                    "type": "session_meta",
                    "payload": {"id": session_id},
                },
                import_title_record(session_id, "Local merge title", "2026-04-30T18:20:39Z"),
                import_user_message("common body", "2026-04-30T18:21:39Z"),
            ]
            incoming_records = [
                local_records[0],
                import_title_record(session_id, "Incoming merge title", "2026-04-30T18:20:39Z"),
                *local_records[2:],
                import_user_message("incoming tail", "2026-04-30T18:22:39Z"),
            ]
            write_jsonl(target_path, local_records)
            write_jsonl(source_path, incoming_records)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    ["import", "--merge", "--codex-home", str(codex_home), str(source_path)]
                )

            output = buffer.getvalue()
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            rollout_backups = tuple(
                (codex_home / "backups" / "codex-sessions").glob(f"*/{target_path.name}")
            )
            self.assertEqual(result, 0)
            self.assertIn("Sessions added: 0", output)
            self.assertIn("Fast-forwarded: 1", output)
            self.assertIn("Rollout:", output)
            self.assertIn("Titles updated:", output)
            self.assertIn("From: Local merge title", output)
            self.assertIn("To:   Incoming merge title", output)
            self.assertEqual(read_jsonl(target_path), incoming_records)
            self.assertEqual(index_records[0]["thread_name"], "Incoming merge title")
            self.assertEqual(index_records[0]["updated_at"], "2026-04-30T18:22:39Z")
            self.assertEqual(index_records[0]["extra"], "preserved")
            self.assertIn("State database rebuild skipped.", output)
            self.assertTrue(state_db.exists())
            self.assertEqual(
                list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite")),
                [],
            )
            self.assertEqual(len(rollout_backups), 1)
            self.assertEqual(read_jsonl(rollout_backups[0]), local_records)

    def test_import_merge_dry_run_reports_fast_forward_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "55555555-5454-5454-5454-545454545454"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            local_records = [
                {"type": "session_meta", "payload": {"id": session_id}},
                import_user_message("common body", "2026-04-30T18:21:39Z"),
            ]
            incoming_records = [*local_records, import_user_message("tail", "2026-04-30T18:22:39Z")]
            write_jsonl(target_path, local_records)
            write_jsonl(source_path, incoming_records)
            original_target = target_path.read_text(encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "import",
                        "--merge",
                        "--dry-run",
                        "--codex-home",
                        str(codex_home),
                        str(source_path),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Would fast-forward: 1", output)
            self.assertIn("Would fast-forward sessions:", output)
            self.assertEqual(target_path.read_text(encoding="utf-8"), original_target)
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_merge_skips_equivalent_and_local_ahead_rollouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            equivalent_id = "56565656-5656-5656-5656-565656565656"
            local_ahead_id = "57575757-5757-5757-5757-575757575757"
            equivalent_source = source_dir / (f"rollout-2026-04-30T18-20-39-{equivalent_id}.jsonl")
            local_ahead_source = source_dir / (
                f"rollout-2026-04-30T18-21-39-{local_ahead_id}.jsonl"
            )
            equivalent_common = [
                {"type": "session_meta", "payload": {"id": equivalent_id}},
                import_user_message("equivalent", "2026-04-30T18:20:40Z"),
            ]
            write_jsonl(
                sessions_day / equivalent_source.name,
                [
                    *equivalent_common,
                    import_title_record(
                        equivalent_id, "Local equivalent title", "2026-04-30T18:20:39Z"
                    ),
                ],
            )
            write_jsonl(
                equivalent_source,
                [
                    *equivalent_common,
                    import_title_record(
                        equivalent_id, "Incoming equivalent title", "2026-04-30T18:20:41Z"
                    ),
                ],
            )
            local_ahead_common = [
                {"type": "session_meta", "payload": {"id": local_ahead_id}},
                import_user_message("common ahead", "2026-04-30T18:21:40Z"),
            ]
            write_jsonl(local_ahead_source, local_ahead_common)
            write_jsonl(
                sessions_day / local_ahead_source.name,
                [*local_ahead_common, import_user_message("local tail", "2026-04-30T18:22:40Z")],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    ["import", "--merge", "--codex-home", str(codex_home), str(source_dir)]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Skipped (equivalent): 1", output)
            self.assertIn("Skipped (local ahead): 1", output)
            self.assertIn("SKIPPED (equivalent)", output)
            self.assertIn("SKIPPED (local ahead)", output)
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_merge_reports_diverged_rollouts_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "58585858-5858-5858-5858-585858585858"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            common_records = [{"type": "session_meta", "payload": {"id": session_id}}]
            local_records = [*common_records, import_user_message("local", "2026-04-30T18:21:39Z")]
            write_jsonl(target_path, local_records)
            write_jsonl(
                source_path,
                [*common_records, import_user_message("incoming", "2026-04-30T18:21:39Z")],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    ["import", "--merge", "--codex-home", str(codex_home), str(source_path)]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("Diverged conflicts: 1", output)
            self.assertIn(f"Diverged {session_id}", output)
            self.assertIn("Common records: 1", output)
            self.assertIn("Local:", output)
            self.assertIn("Import:", output)
            self.assertIn("Tail records: 1", output)
            self.assertIn("local", output)
            self.assertIn("incoming", output)
            self.assertNotIn("First differing records:", output)
            self.assertEqual(read_jsonl(target_path), local_records)
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_merge_can_preview_first_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "58585858-6868-6868-6868-686868686868"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            common_records = [{"type": "session_meta", "payload": {"id": session_id}}]
            local_records = [
                *common_records,
                import_user_message("local branch message", "2026-04-30T18:21:39Z"),
            ]
            incoming_records = [
                *common_records,
                import_user_message("incoming branch message", "2026-04-30T18:21:39Z"),
            ]
            write_jsonl(target_path, local_records)
            write_jsonl(source_path, incoming_records)

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "import",
                        "--merge",
                        "--show-divergence",
                        "--codex-home",
                        str(codex_home),
                        str(source_path),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("First differing records:", output)
            self.assertIn("Local first differing record: response_item", output)
            self.assertIn("Import first differing record: response_item", output)
            self.assertIn("User: local branch message", output)
            self.assertIn("User: incoming branch message", output)

    def test_import_merge_keeps_fast_forward_and_state_db_without_automatic_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "59595959-5959-5959-5959-595959595959"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "Rollback merge title",
                        "updated_at": "2026-04-30T18:21:39Z",
                    }
                ],
            )
            local_records = [
                {"type": "session_meta", "payload": {"id": session_id}},
                import_user_message("common", "2026-04-30T18:21:39Z"),
            ]
            write_jsonl(target_path, local_records)
            incoming_records = [
                *local_records,
                import_user_message("incoming tail", "2026-04-30T18:22:39Z"),
            ]
            write_jsonl(source_path, incoming_records)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    ["import", "--merge", "--codex-home", str(codex_home), str(source_path)]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State database rebuild skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertTrue(state_db.exists())
            self.assertEqual(
                list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite")),
                [],
            )
            self.assertEqual(read_jsonl(index_path)[0]["updated_at"], "2026-04-30T18:22:39Z")
            target_records = read_jsonl(target_path)
            self.assertEqual(target_records[0], incoming_records[0])
            self.assertEqual(target_records[1]["payload"]["type"], "thread_name_updated")
            self.assertEqual(target_records[1]["payload"]["thread_name"], "Rollback merge title")
            self.assertEqual(target_records[2:], incoming_records[1:])
            self.assertEqual(len(list((codex_home / "backups").rglob(target_path.name))), 1)


if __name__ == "__main__":
    unittest.main()
