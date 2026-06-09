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


class CliExportErrorsTests(unittest.TestCase):
    def test_export_refuses_existing_output_unless_force_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_path = root / "session.jsonl"
            output_path.write_text("existing", encoding="utf-8")
            session_id = "26262626-2626-2626-2626-262626262626"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Force export title"}],
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
                            "thread_name": "Force export title",
                        },
                    }
                ],
            )

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_path),
                    ]
                )

            self.assertIn("Output file already exists", str(raised.exception))

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "export",
                        "--force",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(output_path),
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"), rollout_path.read_text(encoding="utf-8")
            )

    def test_export_refuses_output_path_equal_to_source_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "33333333-3333-3333-3333-333333333333"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Source output refusal"}],
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
                            "thread_name": "Source output refusal",
                        },
                    }
                ],
            )

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(rollout_path),
                    ]
                )

            self.assertIn("Export output path is the source rollout file", str(raised.exception))

    def test_export_refuses_multiple_rollout_files_for_same_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            first_day = codex_home / "sessions" / "2026" / "04" / "30"
            second_day = codex_home / "sessions" / "2026" / "05" / "01"
            first_day.mkdir(parents=True)
            second_day.mkdir(parents=True)
            session_id = "34343434-3434-3434-3434-343434343434"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Duplicate rollout export"}],
            )
            for index, day in enumerate((first_day, second_day), start=1):
                write_jsonl(
                    day / f"rollout-2026-04-{29 + index:02d}T18-20-39-{session_id}.jsonl",
                    [
                        {
                            "timestamp": "2026-04-30T18:20:39Z",
                            "type": "session_meta",
                            "payload": {"id": session_id},
                        }
                    ],
                )

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        session_id,
                        "-o",
                        str(root / "out"),
                    ]
                )

            self.assertIn("Multiple Codex session files found", str(raised.exception))

    def test_export_refuses_duplicate_exact_title_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex"
            codex_home.mkdir()
            (codex_home / "sessions").mkdir()
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {
                        "id": "35353535-3535-3535-3535-353535353535",
                        "thread_name": "Duplicate exact title",
                    },
                    {
                        "id": "36363636-3636-3636-3636-363636363636",
                        "thread_name": "Duplicate exact title",
                    },
                ],
            )

            with self.assertRaises(SystemExit) as raised:
                main(["export", "--codex-home", str(codex_home), "Duplicate exact title"])

            self.assertIn(
                "Multiple session_index.jsonl entries matched title", str(raised.exception)
            )
