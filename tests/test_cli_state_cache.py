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


class CliStateCacheTests(unittest.TestCase):
    def test_reset_state_cache_command_backs_up_live_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_wal = codex_home / "state_5.sqlite-wal"
            state_db.write_text("state", encoding="utf-8")
            state_wal.write_text("wal", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["reset-state-cache", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(result, 0)
            self.assertIn("Backups:", output)
            self.assertFalse(state_db.exists())
            self.assertFalse(state_wal.exists())
            self.assertEqual(len(backup_dirs), 1)
            self.assertEqual((backup_dirs[0] / state_db.name).read_text(encoding="utf-8"), "state")
            self.assertEqual((backup_dirs[0] / state_wal.name).read_text(encoding="utf-8"), "wal")

    def test_reset_state_cache_command_fails_when_reset_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)

            with patch(
                "codex_sessions.cli.reset_codex_state_cache_with_backup",
                side_effect=OSError("locked"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    main(["reset-state-cache", "--codex-home", str(codex_home)])

            self.assertIn("locked", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
