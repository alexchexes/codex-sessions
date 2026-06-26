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


class CliFindContentTests(unittest.TestCase):
    def test_find_searches_deserialized_text_and_groups_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "77777777-7777-7777-7777-777777777777"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Dadata integration"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": 'Install "dadata-sdk"\nThen run it',
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "-i", "dadata-sdk", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Dadata integration", output)
            self.assertIn('Install "dadata-sdk"', output)
            self.assertNotIn("Then run it", output)
            self.assertNotIn("\\n", output)
            self.assertNotIn('\\"', output)

    def test_find_searches_indexed_session_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "39393939-3939-3939-3939-393939393939"
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
                result = main(["find", "Explore user input", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Explore user input options", output)
            self.assertNotIn("User:", output)

    def test_find_searches_unindexed_rollout_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "40404040-4040-4040-4040-404040404040"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Explore user input options",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Discuss a different topic.",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "Explore user input", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Explore user input options", output)
            self.assertIn("NO ENTRY IN session_index.jsonl", output)
            self.assertNotIn("User:", output)

    def test_find_infers_title_for_unindexed_rollout_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "34343434-3434-3434-3434-343434343434"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Hand off this session to a Mac.",
                        },
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "Mac", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn(f"{session_id} - Hand off this session to a Mac.", output)
            self.assertIn("NO ENTRY IN session_index.jsonl", output)

    def test_find_searches_visible_messages_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Repo investigation"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "cwd": r"d:\repos\copy-as-markdown",
                            "base_instructions": {
                                "text": "Large raw instructions mentioning copy-as-markdown"
                            },
                            "git": {
                                "branch": "main",
                                "repository_url": (
                                    "https://github.com/yorkxin/copy-as-markdown.git"
                                ),
                            },
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "Discuss copy-as-markdown behavior",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:22:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "shell_command",
                            "arguments": (
                                '{"command":"Get-Content package.json",'
                                '"workdir":"d:\\\\repos\\\\copy-as-markdown"}'
                            ),
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:22:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "output": "copy-as-markdown should not be searched in outputs",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "copy-as-markdown", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Codex: Discuss copy-as-markdown behavior", output)
            self.assertNotIn("Session metadata:", output)
            self.assertNotIn(r"cwd: d:\repos\copy-as-markdown", output)
            self.assertNotIn(
                "repository_url: https://github.com/yorkxin/copy-as-markdown.git", output
            )
            self.assertNotIn("base_instructions", output)
            self.assertNotIn("Large raw instructions", output)
            self.assertNotIn("should not be searched in outputs", output)
            self.assertNotIn("Tool call", output)

    def test_find_metadata_and_tools_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
            session_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                session_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "cwd": r"d:\repos\copy-as-markdown",
                            "git": {
                                "repository_url": (
                                    "https://github.com/yorkxin/copy-as-markdown.git"
                                )
                            },
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "shell_command",
                            "arguments": (
                                '{"command":"rg copy-as-markdown",'
                                '"workdir":"d:\\\\repos\\\\copy-as-markdown"}'
                            ),
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": json.dumps({"stdout": "output-only-needle", "stderr": ""}),
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                default_result = main(["find", "copy-as-markdown", "--codex-home", str(codex_home)])
            self.assertEqual(default_result, 1)
            self.assertEqual(buffer.getvalue(), "")

            buffer = StringIO()
            with redirect_stdout(buffer):
                metadata_result = main(
                    ["find", "--metadata", "copy-as-markdown", "--codex-home", str(codex_home)]
                )
            self.assertEqual(metadata_result, 0)
            self.assertIn("Session metadata:", buffer.getvalue())

            buffer = StringIO()
            with redirect_stdout(buffer):
                tools_result = main(
                    ["find", "--tools", "copy-as-markdown", "--codex-home", str(codex_home)]
                )
            self.assertEqual(tools_result, 0)
            self.assertIn("Tool call: shell_command", buffer.getvalue())

            buffer = StringIO()
            with redirect_stdout(buffer):
                tool_output_result = main(
                    [
                        "find",
                        "--tools",
                        "output-only-needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )
            self.assertEqual(tool_output_result, 0)
            self.assertIn("Tool output: shell_command", buffer.getvalue())

            buffer = StringIO()
            with redirect_stdout(buffer):
                tool_input_only_result = main(
                    [
                        "find",
                        "--search-in",
                        "tool-inputs",
                        "output-only-needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )
            self.assertEqual(tool_input_only_result, 1)
            self.assertEqual(buffer.getvalue(), "")

            buffer = StringIO()
            with redirect_stdout(buffer):
                tool_output_only_result = main(
                    [
                        "find",
                        "--search-in",
                        "tool-outputs",
                        "output-only-needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )
            self.assertEqual(tool_output_only_result, 0)
            self.assertIn(
                "Tool output: shell_command: stdout: output-only-needle",
                buffer.getvalue(),
            )

    def test_find_regex_is_case_insensitive_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "88888888-8888-8888-8888-888888888888"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:20:50Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "Need DADATA-SDK setup",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "shell_command",
                            "arguments": '{"command":"npm install DADATA-SDK"}',
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "grep",
                        "-i",
                        "-r",
                        "--line-width",
                        "80",
                        "dadata-[a-z]+",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("DADATA-SDK", output)
            self.assertIn("NO ENTRY IN session_index.jsonl", output)

    def test_find_returns_one_when_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            codex_home.joinpath("sessions").mkdir()

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "missing", "--codex-home", str(codex_home)])

            self.assertEqual(result, 1)
            self.assertEqual(buffer.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
