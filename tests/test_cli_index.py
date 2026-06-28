import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

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


class CliIndexTests(unittest.TestCase):
    def test_repair_index_skips_filename_fallback_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            rollout_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            rollout_path.write_text(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": "Body"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["repair-index", "--dry-run", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Missing session_index.jsonl entries: 0", output)
            self.assertIn(
                "Skipped rollout files without a canonical session id: 1",
                output,
            )
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_repair_index_dry_run_reports_missing_entries_without_modifying_index(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            indexed_id = "56565656-5656-5656-5656-565656565656"
            missing_id = "67676767-6767-6767-6767-676767676767"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(
                index_path,
                [{"id": indexed_id, "thread_name": "Already indexed"}],
            )
            original_index = index_path.read_text(encoding="utf-8")
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{indexed_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Already indexed message",
                        },
                    }
                ],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-21-39-{missing_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Repair the missing index entry. More details.",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["repair-index", "--dry-run", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(index_path.read_text(encoding="utf-8"), original_index)
            self.assertIn("Missing session_index.jsonl entries: 1", output)
            self.assertIn("Would add:", output)
            self.assertIn(f"{missing_id} - Repair the missing index entry.", output)
            self.assertIn("2026/04/30/rollout-2026-04-30T18-21-39-", output)
            self.assertIn("updated_at: 2026-04-30T18:21:39+00:00", output)
            self.assertIn("State cache reset required after repair.", output)
            self.assertNotIn(indexed_id, output)

    def test_repair_index_prefers_thread_name_updated_title_from_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            missing_id = "57575757-5757-5757-5757-575757575757"
            write_jsonl(codex_home / "session_index.jsonl", [])
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-21-39-{missing_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": missing_id,
                            "thread_name": "Repair uses rollout title",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:22:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "This fallback title should not be used.",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["repair-index", "--dry-run", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{missing_id} - Repair uses rollout title", output)
            self.assertNotIn("This fallback title", output)

    def test_repair_index_dry_run_reports_no_missing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "78787878-7878-7878-7878-787878787878"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Already indexed"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Already indexed message",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["repair-index", "--dry-run", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Missing session_index.jsonl entries: 0", output)
            self.assertIn("No missing session_index.jsonl entries found.", output)
            self.assertNotIn("State cache reset required", output)

    def test_repair_index_adds_entries_and_resets_state_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            missing_id = "89898989-8989-8989-8989-898989898989"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": "11111111-1111-1111-1111-111111111111"}])
            original_index = index_path.read_text(encoding="utf-8")
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-21-39-{missing_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Repair the real index entry.",
                        },
                    }
                ],
            )
            state_db = codex_home / "state_5.sqlite"
            state_wal = codex_home / "state_5.sqlite-wal"
            logs_db = codex_home / "logs_2.sqlite"
            state_db.write_text("state", encoding="utf-8")
            state_wal.write_text("wal", encoding="utf-8")
            logs_db.write_text("logs", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["repair-index", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            index_records = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(result, 0)
            self.assertIn("Added session_index.jsonl entries: 1", output)
            self.assertIn("Backups:", output)
            self.assertIn("Index:", output)
            self.assertIn("Backups:", output)
            self.assertEqual(index_records[-1]["id"], missing_id)
            self.assertEqual(index_records[-1]["thread_name"], "Repair the real index entry.")
            self.assertEqual(index_records[-1]["updated_at"], "2026-04-30T18:21:39Z")
            self.assertFalse(state_db.exists())
            self.assertFalse(state_wal.exists())
            self.assertTrue(logs_db.exists())
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            backup_dir = backup_dirs[0]
            self.assertEqual(
                (backup_dir / "session_index.jsonl").read_text(encoding="utf-8"),
                original_index,
            )
            self.assertEqual((backup_dir / "state_5.sqlite").read_text(encoding="utf-8"), "state")
            self.assertEqual(
                (backup_dir / "state_5.sqlite-wal").read_text(encoding="utf-8"),
                "wal",
            )
            self.assertEqual(list(codex_home.glob("state_5.sqlite.backup-*")), [])
            self.assertEqual(list(codex_home.glob("session_index.jsonl.backup-*")), [])

    def test_repair_index_keeps_index_when_state_reset_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            missing_id = "90909090-9090-9090-9090-909090909090"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": "11111111-1111-1111-1111-111111111111"}])
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-21-39-{missing_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Rollback the failed repair.",
                        },
                    }
                ],
            )

            with patch(
                "codex_sessions.sessions.index_workflows.reset_codex_state_cache",
                side_effect=OSError("locked"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(["repair-index", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("\n  locked", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertEqual(read_jsonl(index_path)[-1]["id"], missing_id)
            self.assertEqual(
                len(list(codex_home.glob("backups/codex-sessions/*/session_index.jsonl"))),
                1,
            )
