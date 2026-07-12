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


class CliRenameTests(unittest.TestCase):
    def test_rename_updates_archived_index_and_rollout_without_moving_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            archived_dir = codex_home / "archived_sessions"
            archived_dir.mkdir()
            session_id = "abababab-abab-abab-abab-abababababab"
            index_path = codex_home / "session_index.jsonl"
            rollout_path = archived_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(index_path, [{"id": session_id, "thread_name": "Old archived title"}])
            write_jsonl(
                rollout_path,
                [import_user_message("Archived body", "2026-04-30T18:20:39Z")],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "rename",
                        "--codex-home",
                        str(codex_home),
                        "--no-reset-state-cache",
                        session_id,
                        "New archived title",
                    ]
                )

            rollout_records = read_jsonl(rollout_path)
            title_events = [
                record
                for record in rollout_records
                if record.get("type") == "event_msg"
                and record.get("payload", {}).get("type") == "thread_name_updated"
            ]

            self.assertEqual(result, 0)
            self.assertEqual(read_jsonl(index_path)[0]["thread_name"], "New archived title")
            self.assertEqual(title_events[-1]["payload"]["thread_name"], "New archived title")
            self.assertTrue(rollout_path.exists())
            self.assertFalse((codex_home / "sessions").exists())
            self.assertIn(str(rollout_path), buffer.getvalue())

    def test_rename_refuses_filename_fallback_without_partial_index_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(
                index_path,
                [{"id": session_id, "thread_name": "Original title"}],
            )
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

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "rename",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "New title",
                    ]
                )

            self.assertIn("invalid canonical session metadata", str(raised.exception))
            self.assertEqual(read_jsonl(index_path)[0]["thread_name"], "Original title")

    def test_rename_updates_index_without_automatic_state_rebuild(self) -> None:
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
            self.assertIn("Index backup:", output)
            self.assertIn("State database rebuild skipped.", output)
            self.assertEqual(index_records[0]["thread_name"], "New title")
            self.assertEqual(index_records[0]["updated_at"], "2026-04-30T18:21:39Z")
            self.assertEqual(index_records[0]["extra"], "preserved")
            self.assertTrue(state_db.exists())
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(len(backup_dirs), 1)
            backup_dir = backup_dirs[0]
            self.assertEqual(
                (backup_dir / "session_index.jsonl").read_text(encoding="utf-8"),
                original_index,
            )
            self.assertFalse((backup_dir / "state_5.sqlite").exists())
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
                rollout_records[1]["payload"]["thread_name"],
                "New title",
            )
            self.assertEqual(len(backup_dirs), 1)
            backup_dir = backup_dirs[0]
            self.assertFalse((backup_dir / "session_index.jsonl").exists())
            self.assertEqual(
                (backup_dir / rollout_path.name).read_text(encoding="utf-8"),
                original_rollout,
            )
            self.assertTrue(state_db.exists())
            self.assertFalse((backup_dir / "state_5.sqlite").exists())

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

    def test_rename_non_tty_keeps_state_database_and_prints_explicit_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1414-1414-1414-141414141414"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": session_id, "thread_name": "Old title"}])
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")
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
            self.assertIn("State database rebuild skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertEqual(read_jsonl(index_path)[0]["thread_name"], "New title")
            self.assertEqual(state_db.read_text(encoding="utf-8"), "state")
            self.assertEqual(
                len(list(codex_home.glob("backups/codex-sessions/*/session_index.jsonl"))),
                1,
            )

    def test_rename_interactive_yes_rebuilds_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with (
                patch(
                    "codex_sessions.cli.can_prompt_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch("builtins.input", return_value="y"),
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
            self.assertIn("Optional Codex state database rebuild:", output)
            self.assertIn("Rebuilding Codex state database...", output)
            self.assertIn("State database rebuild OK.", output)
            self.assertFalse(state_db.exists())
            self.assertEqual(
                len(list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite"))),
                1,
            )

    def test_rename_interactive_blank_defaults_to_no_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with (
                patch(
                    "codex_sessions.cli.can_prompt_state_cache_reset_interactively",
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
            self.assertIn("Optional Codex state database rebuild:", output)
            self.assertIn("State database rebuild skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertNotIn("Rebuilding Codex state database...", output)
            self.assertEqual(state_db.read_text(encoding="utf-8"), "state")

    def test_rename_no_reset_flag_suppresses_interactive_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            session_id = "14141414-1515-1616-1717-181818181818"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title"}],
            )

            with (
                patch(
                    "codex_sessions.cli.can_prompt_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch("builtins.input") as prompt,
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(
                        [
                            "rename",
                            "--codex-home",
                            str(codex_home),
                            "--no-reset-state-cache",
                            session_id,
                            "New title",
                        ]
                    )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State database rebuild skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertNotIn("Optional Codex state database rebuild:", output)
            prompt.assert_not_called()
