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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


class CliExportIdentityTests(unittest.TestCase):
    def test_export_preserves_degraded_filename_id_rollout_bytes_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            source_path = (
                codex_home
                / "sessions"
                / "2026"
                / "04"
                / "30"
                / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            )
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Degraded backup"}],
            )
            write_jsonl(
                source_path,
                [
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": "Body"},
                    }
                ],
            )
            source_bytes = source_path.read_bytes()
            output_path = root / "backup.jsonl"

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--output",
                        str(output_path),
                        session_id,
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(output_path.read_bytes(), source_bytes)
            self.assertIn("INVALID RECORD-1 session_meta; USING ID FROM FILENAME", output)
            self.assertIn("copy unchanged", output)
            self.assertIn("using trailing filename session ID", output)

    def test_bulk_export_continues_after_no_id_failure_and_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            valid_id = "11111111-2222-3333-4444-555555555555"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{valid_id}.jsonl",
                [{"type": "session_meta", "payload": {"id": valid_id}}],
            )
            invalid_path = sessions_day / "rollout-without-id.jsonl"
            write_jsonl(
                invalid_path,
                [
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": "Preserve me"},
                    }
                ],
            )
            output_dir = root / "exports"

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--all",
                        "--codex-home",
                        str(codex_home),
                        "--output",
                        str(output_dir),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertEqual(len(list(output_dir.glob("*.jsonl"))), 1)
            self.assertTrue(invalid_path.exists())
            self.assertIn("Exported sessions: 1", output)
            self.assertIn("Failed:", output)
            self.assertIn(str(invalid_path), output)
            self.assertIn("no trailing filename session ID is available", output)

    def test_time_filtered_export_ignores_out_of_range_no_id_failure_when_no_sessions_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            invalid_path = sessions_day / "rollout-without-id.jsonl"
            write_jsonl(
                invalid_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Out of range",
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
                        "--updated-after",
                        "2026-05-01",
                        "--output",
                        str(root / "exports"),
                    ]
                )

            self.assertIn("No sessions matched export selection", str(raised.exception))
            self.assertFalse((root / "exports").exists())

    def test_time_filtered_export_ignores_out_of_range_no_id_failure_with_matching_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            old_day = codex_home / "sessions" / "2026" / "04" / "30"
            new_day = codex_home / "sessions" / "2026" / "05" / "02"
            old_day.mkdir(parents=True)
            new_day.mkdir(parents=True)
            session_id = "12121212-3434-5656-7878-909090909090"
            invalid_path = old_day / "rollout-without-id.jsonl"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Matching filtered export"}],
            )
            write_jsonl(
                invalid_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Out of range",
                        },
                    }
                ],
            )
            write_jsonl(
                new_day / f"rollout-2026-05-02T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-05-02T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    }
                ],
            )
            output_dir = root / "exports"

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--updated-after",
                        "2026-05-01",
                        "--output",
                        str(output_dir),
                    ]
                )

            output = buffer.getvalue()
            exported_files = list(output_dir.glob("*.jsonl"))
            self.assertEqual(result, 0)
            self.assertEqual(len(exported_files), 1)
            self.assertIn("Exported:", output)
            self.assertIn(session_id, output)
            self.assertNotIn("Failed:", output)
            self.assertNotIn(str(invalid_path), output)

    def test_time_filtered_export_reports_in_range_no_id_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "02"
            sessions_day.mkdir(parents=True)
            invalid_path = sessions_day / "rollout-without-id.jsonl"
            write_jsonl(
                invalid_path,
                [
                    {
                        "timestamp": "2026-05-02T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "In range",
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
                        "--updated-after",
                        "2026-05-01",
                        "--output",
                        str(root / "exports"),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("Exported sessions: 0", output)
            self.assertIn("Failed:", output)
            self.assertIn(str(invalid_path), output)
            self.assertIn("no trailing filename session ID is available", output)

    def test_time_filtered_export_reports_in_range_malformed_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "02"
            sessions_day.mkdir(parents=True)
            malformed_path = sessions_day / "rollout-malformed.jsonl"
            malformed_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-05-02T18:20:39Z",
                                "type": "response_item",
                                "payload": {
                                    "type": "message",
                                    "role": "user",
                                    "content": "In range before malformed line",
                                },
                            }
                        ),
                        "{not valid json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--updated-after",
                        "2026-05-01",
                        "--output",
                        str(root / "exports"),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("Exported sessions: 0", output)
            self.assertIn("Failed:", output)
            self.assertIn(str(malformed_path), output)
            self.assertIn("Invalid JSON on line 2", output)


if __name__ == "__main__":
    unittest.main()
