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
from codex_sessions.sessions.cache import (  # noqa: E402
    session_cache_path,
)


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


class CliImportApplyTests(unittest.TestCase):
    def test_import_dry_run_reports_plan_without_modifying_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "17171717-1717-1717-1717-171717171717"
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Import dry run title",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    ["import", "--dry-run", "--codex-home", str(codex_home), str(source_path)]
                )

            output = buffer.getvalue()
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / source_path.name
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Import dry run title", output)
            self.assertIn("Target:", output)
            self.assertIn(str(target_path), output)
            self.assertIn("Index action: add session_index.jsonl entry", output)
            self.assertIn("Action:      copy unchanged", output)
            self.assertIn("Fingerprint:", output)
            self.assertFalse(target_path.exists())
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_bare_rollout_adds_index_copies_rollout_and_resets_state_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "18181818-1818-1818-1818-181818181818"
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            records = [
                {
                    "timestamp": "2026-04-30T18:20:39Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_name_updated",
                        "thread_id": session_id,
                        "thread_name": "Imported title",
                    },
                },
                {
                    "timestamp": "2026-04-30T18:21:39Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": "Imported body",
                    },
                },
            ]
            write_jsonl(source_path, records)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / source_path.name
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(result, 0)
            self.assertIn("Imported session:", output)
            self.assertIn(f"{session_id} - Imported title", output)
            self.assertIn("Index action: add session_index.jsonl entry", output)
            self.assertIn("Backups:", output)
            self.assertEqual(
                target_path.read_text(encoding="utf-8"), source_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                index_records,
                [
                    {
                        "id": session_id,
                        "thread_name": "Imported title",
                        "updated_at": "2026-04-30T18:21:39Z",
                    }
                ],
            )
            self.assertFalse(state_db.exists())
            self.assertEqual(len(backup_dirs), 1)
            self.assertEqual(
                (backup_dirs[0] / "state_5.sqlite").read_text(encoding="utf-8"), "state"
            )

    def test_import_can_skip_state_cache_reset_for_scripted_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "18181818-2929-2929-2929-292929292929"
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Deferred reset import",
                        },
                    }
                ],
            )
            state_db = codex_home / "state_5.sqlite"
            state_db.parent.mkdir(parents=True)
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "import",
                        "--no-reset-state-cache",
                        "--codex-home",
                        str(codex_home),
                        str(source_path),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset skipped.", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertTrue(state_db.exists())
            self.assertEqual(
                list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite")),
                [],
            )

    def test_import_inserts_rollout_title_event_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "19191919-1919-1919-1919-191919191919"
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                source_path,
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
                            "content": "Infer this imported title. More body.",
                        },
                    },
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            target_path = codex_home / "sessions" / "2026" / "04" / "30" / source_path.name
            rollout_records = read_jsonl(target_path)
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 0)
            self.assertEqual(rollout_records[0]["type"], "session_meta")
            self.assertEqual(rollout_records[1]["type"], "event_msg")
            self.assertEqual(rollout_records[1]["timestamp"], "2026-04-30T18:20:39Z")
            self.assertEqual(
                rollout_records[1]["payload"]["thread_name"], "Infer this imported title."
            )
            self.assertEqual(index_records[0]["thread_name"], "Infer this imported title.")

    def test_import_dry_run_does_not_persist_existing_rollout_fingerprint_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "21212121-3232-3232-3232-323232323232"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            write_jsonl(
                source_path,
                [
                    import_title_record(
                        session_id, "Incoming dry cache conflict", "2026-04-30T18:20:38Z"
                    ),
                    import_user_message("incoming", "2026-04-30T18:20:39Z"),
                ],
            )
            write_jsonl(
                target_path,
                [
                    import_title_record(
                        session_id, "Local dry cache conflict", "2026-04-30T18:20:38Z"
                    ),
                    import_user_message("local", "2026-04-30T18:20:39Z"),
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    ["import", "--dry-run", "--codex-home", str(codex_home), str(source_path)]
                )

            self.assertEqual(result, 1)
            self.assertFalse(session_cache_path(codex_home).exists())

    def test_import_existing_index_without_rollout_uses_existing_index_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "22222222-2222-2222-2222-222222222222"
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Existing index title"}],
            )
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Source rollout title",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            target_path = codex_home / "sessions" / "2026" / "04" / "30" / source_path.name
            rollout_records = read_jsonl(target_path)
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 0)
            self.assertEqual(rollout_records[0]["payload"]["thread_name"], "Existing index title")
            self.assertEqual(
                index_records, [{"id": session_id, "thread_name": "Existing index title"}]
            )

    def test_import_keeps_index_and_target_rollout_when_state_reset_is_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "28282828-2828-2828-2828-282828282828"
            index_path = codex_home / "session_index.jsonl"
            write_jsonl(index_path, [{"id": "11111111-1111-1111-1111-111111111111"}])
            source_path = source_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Rollback import title",
                        },
                    }
                ],
            )
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / source_path.name

            with patch(
                "codex_sessions.sessions.transfer.reset_codex_state_cache",
                side_effect=OSError("locked"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("State cache reset deferred:", output)
            self.assertIn("\n  locked", output)
            self.assertIn("codex-sessions reset-state-cache", output)
            self.assertEqual(read_jsonl(index_path)[-1]["id"], session_id)
            self.assertTrue(target_path.exists())
            self.assertEqual(
                len(list(codex_home.glob("backups/codex-sessions/*/session_index.jsonl"))),
                1,
            )
