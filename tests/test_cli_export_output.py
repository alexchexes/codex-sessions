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


class CliExportOutputTests(unittest.TestCase):
    def test_export_dry_run_reports_plan_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_path = root / "session.jsonl"
            session_id = "25252525-2525-2525-2525-252525252525"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Dry export title"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--dry-run",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_path),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Dry export title", output)
            self.assertIn("Output:", output)
            self.assertIn(str(output_path), output)
            self.assertIn("Action: copy with rollout title event update", output)
            self.assertFalse(output_path.exists())

    def test_export_readable_filename_sanitizes_and_truncates_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            output_dir = root / "exports"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "37373737-3737-3737-3737-373737373737"
            long_title = "Need: punctuation / spaces? " + ("word " * 30)
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": long_title}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
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

            exported_files = list(output_dir.glob("*.jsonl"))
            self.assertEqual(result, 0)
            self.assertEqual(len(exported_files), 1)
            self.assertTrue(
                exported_files[0].name.startswith("2026-04-30--Need-punctuation-spaces-word")
            )
            self.assertTrue(exported_files[0].name.endswith(f"--{session_id}.jsonl"))
            title_part = (
                exported_files[0]
                .name.removeprefix("2026-04-30--")
                .removesuffix(f"--{session_id}.jsonl")
            )
            self.assertLessEqual(len(title_part), 80)

    def test_export_then_import_round_trips_title_and_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_home = root / "source"
            target_home = root / "target"
            export_dir = root / "exports"
            sessions_day = source_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            target_home.mkdir()
            session_id = "38383838-3838-3838-3838-383838383838"
            write_jsonl(
                source_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Round trip index title"}],
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
                            "content": "Round-trip body",
                        },
                    },
                ],
            )
            state_db = target_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with redirect_stdout(StringIO()):
                export_result = main(
                    [
                        "export",
                        "--codex-home",
                        str(source_home),
                        session_id,
                        "-o",
                        str(export_dir),
                    ]
                )
            exported_path = export_dir / f"2026-04-30--Round-trip-index-title--{session_id}.jsonl"
            with redirect_stdout(StringIO()):
                import_result = main(
                    ["import", "--codex-home", str(target_home), str(exported_path)]
                )

            target_rollouts = list((target_home / "sessions").rglob("*.jsonl"))
            target_records = read_jsonl(target_rollouts[0])
            index_records = read_jsonl(target_home / "session_index.jsonl")
            self.assertEqual(export_result, 0)
            self.assertEqual(import_result, 0)
            self.assertEqual(len(target_rollouts), 1)
            self.assertEqual(index_records[0]["thread_name"], "Round trip index title")
            self.assertEqual(target_records[0]["payload"]["thread_name"], "Round trip index title")
            self.assertEqual(target_records[1]["payload"]["content"], "Round-trip body")
            self.assertFalse(state_db.exists())
            state_backups = tuple(
                (target_home / "backups" / "codex-sessions").glob("*/state_5.sqlite")
            )
            self.assertEqual(len(state_backups), 1)
            self.assertEqual(state_backups[0].read_text(encoding="utf-8"), "state")


if __name__ == "__main__":
    unittest.main()
