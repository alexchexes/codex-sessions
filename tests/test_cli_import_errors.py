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


class CliImportErrorTests(unittest.TestCase):
    def test_import_rejects_filename_id_when_record_one_metadata_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            source_path = root / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path.write_text(
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
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            self.assertEqual(result, 1)
            self.assertIn("Invalid Codex rollout identity", buffer.getvalue())
            self.assertFalse((codex_home / "sessions").exists())

    def test_import_reports_missing_input_without_writing_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex"
            missing_path = Path(tmpdir) / "missing.jsonl"

            with self.assertRaises(SystemExit) as raised:
                main(["import", "--codex-home", str(codex_home), str(missing_path)])

            self.assertIn("Input file not found", str(raised.exception))
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_reports_empty_directory_input_without_writing_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()

            with self.assertRaises(SystemExit) as raised:
                main(["import", "--codex-home", str(codex_home), str(source_dir)])

            self.assertIn("No rollout JSONL files found in import directory", str(raised.exception))
            self.assertFalse((codex_home / "session_index.jsonl").exists())

    def test_import_reports_rollout_without_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_path = root / "rollout-2026-04-30T18-20-39.jsonl"
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "No ID here",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("Failed: 1", output)
            self.assertIn("Invalid Codex rollout identity", output)
            self.assertIn("record 1 must be session_meta", output)
            self.assertFalse((codex_home / "session_index.jsonl").exists())
