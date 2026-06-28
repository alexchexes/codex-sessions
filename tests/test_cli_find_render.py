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


class CliFindRenderTests(unittest.TestCase):
    def test_find_color_always_highlights_title_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "41414141-4141-4141-4141-414141414141"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Explore user input options"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Discuss a different topic.",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--color",
                        "always",
                        "Explore user input",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("\x1b[1;91m", output)
            self.assertIn("Explore user input", output)

    def test_find_truncates_long_matching_lines_with_multiple_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
            long_line = f"{'a' * 80} copy-as-markdown {'b' * 80} copy-as-markdown {'c' * 80}"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": long_line,
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--line-width",
                        "90",
                        "copy-as-markdown",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            matching_lines = [line for line in output.splitlines() if "copy-as-markdown" in line]
            self.assertEqual(result, 0)
            self.assertEqual(len(matching_lines), 1)
            self.assertLessEqual(len(matching_lines[0]), 92)
            self.assertIn("...", matching_lines[0])
            self.assertEqual(matching_lines[0].count("copy-as-markdown"), 2)

    def test_find_uses_available_width_for_single_match_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
            long_line = f"{'a' * 100} copy-as-markdown {'b' * 100}"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": long_line,
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--line-width",
                        "120",
                        "copy-as-markdown",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            matching_lines = [line for line in output.splitlines() if "copy-as-markdown" in line]
            self.assertEqual(result, 0)
            self.assertEqual(len(matching_lines), 1)
            self.assertGreaterEqual(len(matching_lines[0]), 115)
            self.assertLessEqual(len(matching_lines[0]), 122)

    def test_find_summarizes_extra_matches_on_one_long_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
            long_line = (
                "first useful context before copy-as-markdown "
                + " filler text " * 8
                + "second useful context before copy-as-markdown "
                + " filler text " * 8
                + "third copy-as-markdown fourth copy-as-markdown fifth copy-as-markdown"
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": long_line,
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--line-width",
                        "120",
                        "copy-as-markdown",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            matching_lines = [line for line in output.splitlines() if "more on line" in line]
            self.assertEqual(result, 0)
            self.assertEqual(len(matching_lines), 1)
            self.assertLessEqual(len(matching_lines[0]), 122)
            self.assertEqual(matching_lines[0].count("copy-as-markdown"), 1)
            self.assertIn("(+4 more on line)", matching_lines[0])

    def test_find_color_always_highlights_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "99999999-9999-9999-9999-999999999999"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "dadata-sdk",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--color",
                        "always",
                        "dadata-sdk",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("\x1b[", output)
            self.assertIn("\x1b[1;91m", output)
            self.assertIn("dadata-sdk", output)

    def test_find_limits_matching_lines_per_session_with_omission_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            records: list[dict[str, Any]] = [
                {
                    "timestamp": "2026-04-30T18:20:39Z",
                    "type": "session_meta",
                    "payload": {"id": session_id},
                }
            ]
            for index in range(3):
                records.append(
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": f"needle context {index}",
                        },
                    }
                )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                records,
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "needle",
                        "--max-lines-per-session",
                        "2",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("needle context 0", output)
            self.assertIn("needle context 1", output)
            self.assertNotIn("needle context 2", output)
            self.assertIn("+1 more occurrences", output)


if __name__ == "__main__":
    unittest.main()
