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


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
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


class CliRenameTests(unittest.TestCase):
    def test_rename_updates_index_and_resets_state_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "12121212-1212-1212-1212-121212121212"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(
                index_path,
                [
                    {
                        "id": session_id,
                        "thread_name": "Old title",
                        "updated_at": "2026-04-30T18:21:39Z",
                        "extra": "preserved",
                    }
                ],
            )
            original_index = index_path.read_text(encoding="utf-8")
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")
            previous_backup = codex_home / "state_5.sqlite.backup-20260504-112050-16164"
            previous_backup.write_text("previous backup", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "rename",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "New",
                        "title",
                    ]
                )

            output = buffer.getvalue()
            index_records = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(result, 0)
            self.assertIn(f"Renamed session {session_id}", output)
            self.assertIn("From:", output)
            self.assertIn("Old title", output)
            self.assertIn("To:", output)
            self.assertIn("New title", output)
            self.assertIn("Backups:", output)
            self.assertIn("Index backup:", output)
            self.assertIn("Backups:", output)
            self.assertEqual(index_records[0]["thread_name"], "New title")
            self.assertEqual(index_records[0]["updated_at"], "2026-04-30T18:21:39Z")
            self.assertEqual(index_records[0]["extra"], "preserved")
            self.assertFalse(state_db.exists())
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            backup_dir = backup_dirs[0]
            self.assertEqual(
                (backup_dir / "session_index.jsonl").read_text(encoding="utf-8"),
                original_index,
            )
            self.assertEqual((backup_dir / "state_5.sqlite").read_text(encoding="utf-8"), "state")
            self.assertEqual(list(codex_home.glob("session_index.jsonl.backup-*")), [])
            self.assertTrue(previous_backup.exists())
            self.assertEqual(previous_backup.read_text(encoding="utf-8"), "previous backup")
            self.assertEqual(
                list(codex_home.glob("state_5.sqlite.backup-*.backup-*")),
                [],
            )

    def test_rename_updates_rollout_when_index_title_is_already_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "15151515-1515-1515-1515-151515151515"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": session_id, "thread_name": "New title"}])
            rollout_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                rollout_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Old rollout title",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Session body",
                        },
                    },
                ],
            )
            original_rollout = rollout_path.read_text(encoding="utf-8")
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["rename", "--codex-home", str(codex_home), session_id, "New title"])

            output = buffer.getvalue()
            rollout_records = [
                json.loads(line)
                for line in rollout_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(result, 0)
            self.assertIn("From (rollout):", output)
            self.assertIn("Old rollout title", output)
            self.assertEqual(
                rollout_records[0]["payload"]["thread_name"],
                "New title",
            )
            self.assertEqual(len(backup_dirs), 1)
            backup_dir = backup_dirs[0]
            self.assertFalse((backup_dir / "session_index.jsonl").exists())
            self.assertEqual(
                (backup_dir / rollout_path.name).read_text(encoding="utf-8"),
                original_rollout,
            )
            self.assertEqual((backup_dir / "state_5.sqlite").read_text(encoding="utf-8"), "state")

    def test_rename_inserts_rollout_title_event_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "16161616-1616-1616-1616-161616161616"
            write_jsonl(
                codex_home / "session_index.jsonl", [{"id": session_id, "thread_name": "Old"}]
            )
            rollout_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
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
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Session body",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["rename", "--codex-home", str(codex_home), session_id, "New"])

            output = buffer.getvalue()
            rollout_records = [
                json.loads(line)
                for line in rollout_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(result, 0)
            self.assertNotIn("From (rollout):", output)
            self.assertEqual(rollout_records[0]["type"], "session_meta")
            self.assertEqual(rollout_records[1]["type"], "event_msg")
            self.assertEqual(rollout_records[1]["timestamp"], "2026-04-30T18:20:39Z")
            self.assertEqual(rollout_records[1]["payload"]["type"], "thread_name_updated")
            self.assertEqual(rollout_records[1]["payload"]["thread_name"], "New")
            self.assertEqual(rollout_records[-1]["timestamp"], "2026-04-30T18:21:39Z")

    def test_rename_accepts_exact_existing_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "13131313-1313-1313-1313-131313131313"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "Old exact title"}],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "rename",
                        "--codex-home",
                        str(codex_home),
                        "Old exact title",
                        "New exact title",
                    ]
                )

            index_records = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual(result, 0)
            self.assertEqual(index_records[0]["thread_name"], "New exact title")

    def test_rename_keeps_index_when_state_reset_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1414-1414-1414-141414141414"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": session_id, "thread_name": "Old title"}])
            with patch(
                "codex_sessions.sessions.index_workflows.reset_codex_state_cache",
                side_effect=OSError("locked"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(
                        [
                            "rename",
                            "--codex-home",
                            str(codex_home),
                            session_id,
                            "New title",
                        ]
                    )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("\n  locked", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertEqual(read_jsonl(index_path)[0]["thread_name"], "New title")
            self.assertEqual(
                len(list(codex_home.glob("backups/codex-sessions/*/session_index.jsonl"))),
                1,
            )

    def test_rename_interactive_state_reset_retry_reports_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )

            with (
                patch(
                    "codex_sessions.sessions.index_workflows.reset_codex_state_cache",
                    side_effect=OSError("locked"),
                ),
                patch(
                    "codex_sessions.cli.can_retry_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch("builtins.input", return_value=""),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(
                        [
                            "rename",
                            "--codex-home",
                            str(codex_home),
                            session_id,
                            "New title",
                        ]
                    )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("Retrying state cache reset...", output)
            self.assertIn("State cache reset OK.", output)

    def test_rename_interactive_state_reset_prompt_can_skip_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )

            with (
                patch(
                    "codex_sessions.sessions.index_workflows.reset_codex_state_cache",
                    side_effect=OSError("locked"),
                ),
                patch(
                    "codex_sessions.cli.can_retry_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch("builtins.input", return_value="n"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(
                        [
                            "rename",
                            "--codex-home",
                            str(codex_home),
                            session_id,
                            "New title",
                        ]
                    )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("State cache reset skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertNotIn("Retrying state cache reset...", output)

    def test_rename_interactive_state_reset_ctrl_c_skips_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )

            with (
                patch(
                    "codex_sessions.sessions.index_workflows.reset_codex_state_cache",
                    side_effect=OSError("locked"),
                ),
                patch(
                    "codex_sessions.cli.can_retry_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch("builtins.input", side_effect=KeyboardInterrupt),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(
                        [
                            "rename",
                            "--codex-home",
                            str(codex_home),
                            session_id,
                            "New title",
                        ]
                    )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("State cache reset skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertNotIn("Retrying state cache reset...", output)
