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


class CliImportPathTests(unittest.TestCase):
    def test_import_non_rollout_filename_generates_codex_rollout_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_path = root / "session.jsonl"
            session_id = "29292929-2929-2929-2929-292929292929"
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T12:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T12:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Import with generated name.",
                        },
                    },
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            imported_files = list((codex_home / "sessions").rglob("*.jsonl"))
            self.assertEqual(result, 0)
            self.assertEqual(len(imported_files), 1)
            self.assertNotEqual(imported_files[0].name, source_path.name)
            self.assertTrue(imported_files[0].name.startswith("rollout-2026-04-30T"))
            self.assertTrue(imported_files[0].name.endswith(f"-{session_id}.jsonl"))

    def test_import_name_updates_existing_index_without_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()
            source_path = root / "incoming.jsonl"
            session_id = "30303030-3030-3030-3030-303030303030"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Old title", "extra": "preserved"}],
            )
            write_jsonl(
                source_path,
                [
                    {
                        "timestamp": "2026-04-30T12:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T12:21:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Source title",
                        },
                    },
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "import",
                        "--codex-home",
                        str(codex_home),
                        "--name",
                        "Explicit import title",
                        str(source_path),
                    ]
                )

            index_records = read_jsonl(codex_home / "session_index.jsonl")
            imported_files = list((codex_home / "sessions").rglob("*.jsonl"))
            rollout_records = read_jsonl(imported_files[0])
            self.assertEqual(result, 0)
            self.assertEqual(index_records[0]["thread_name"], "Explicit import title")
            self.assertEqual(index_records[0]["extra"], "preserved")
            self.assertEqual(rollout_records[1]["payload"]["thread_name"], "Explicit import title")


if __name__ == "__main__":
    unittest.main()
