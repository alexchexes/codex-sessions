import json
import os
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


class CliExportSingleTests(unittest.TestCase):
    def test_export_by_id_writes_readable_file_with_index_title_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_dir = root / "exports"
            session_id = "23232323-2323-2323-2323-232323232323"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Index title for export"}],
            )
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
                            "content": "Export body",
                        },
                    },
                ],
            )
            original_rollout = rollout_path.read_text(encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_dir),
                    ]
                )

            output = buffer.getvalue()
            exported_path = output_dir / f"2026-04-30--Index-title-for-export--{session_id}.jsonl"
            exported_records = read_jsonl(exported_path)
            source_records = read_jsonl(rollout_path)
            self.assertEqual(result, 0)
            self.assertIn("Exported: ", output)
            self.assertIn(f"{session_id} - Index title for export", output)
            self.assertTrue(exported_path.exists())
            self.assertEqual(
                exported_records[1]["payload"]["thread_name"], "Index title for export"
            )
            self.assertEqual(source_records[1]["payload"]["thread_name"], "Old rollout title")
            self.assertEqual(rollout_path.read_text(encoding="utf-8"), original_rollout)

    def test_export_by_exact_title_to_explicit_file_copies_unchanged_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_path = root / "session.jsonl"
            session_id = "24242424-2424-2424-2424-242424242424"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Exact export title"}],
            )
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
                            "thread_name": "Exact export title",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "-o",
                        str(output_path),
                        "Exact export title",
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Action: copy unchanged", output)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"), rollout_path.read_text(encoding="utf-8")
            )

    def test_export_by_id_without_index_uses_rollout_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_dir = root / "exports"
            session_id = "27272727-2727-2727-2727-272727272727"
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
                            "thread_name": "Rollout only title",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_dir),
                    ]
                )

            exported_path = output_dir / f"2026-04-30--Rollout-only-title--{session_id}.jsonl"
            self.assertEqual(result, 0)
            self.assertEqual(
                exported_path.read_text(encoding="utf-8"),
                rollout_path.read_text(encoding="utf-8"),
            )

    def test_export_without_output_writes_readable_file_to_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            cwd = root / "cwd"
            cwd.mkdir()
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "31313131-3131-3131-3131-313131313131"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Default export output"}],
            )
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
                            "thread_name": "Default export output",
                        },
                    }
                ],
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with redirect_stdout(StringIO()):
                    result = main(["export", "--codex-home", str(codex_home), session_id])
            finally:
                os.chdir(previous_cwd)

            exported_path = cwd / f"2026-04-30--Default-export-output--{session_id}.jsonl"
            self.assertEqual(result, 0)
            self.assertEqual(
                exported_path.read_text(encoding="utf-8"), rollout_path.read_text(encoding="utf-8")
            )

    def test_export_to_non_existing_directory_path_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            output_dir = root / "new-exports"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "32323232-3232-3232-3232-323232323232"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Create export directory"}],
            )
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
                            "thread_name": "Create export directory",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_dir),
                    ]
                )

            exported_path = output_dir / f"2026-04-30--Create-export-directory--{session_id}.jsonl"
            self.assertEqual(result, 0)
            self.assertEqual(
                exported_path.read_text(encoding="utf-8"), rollout_path.read_text(encoding="utf-8")
            )
