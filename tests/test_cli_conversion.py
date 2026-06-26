import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402
from codex_sessions.formats.yaml import convert_jsonl_to_yaml_stream  # noqa: E402
from codex_sessions.sessions.paths import (  # noqa: E402
    default_output_path,
    resolve_output_path,
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


class CliConversionTests(unittest.TestCase):
    def test_yaml_conversion_redacts_encrypted_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.yaml"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "reasoning",
                            "encrypted_content": "secret",
                        },
                    }
                ],
            )

            count = convert_jsonl_to_yaml_stream(input_path, output_path, "...")

            self.assertEqual(count, 1)
            output = output_path.read_text(encoding="utf-8")
            self.assertIn('encrypted_content: "..."', output)
            self.assertNotIn("secret", output)

    def test_default_output_path_goes_under_codex_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            input_path = codex_home / "sessions" / "2026" / "04" / "30" / "rollout.jsonl"

            output_path = default_output_path(input_path, codex_home, "yaml")

            self.assertEqual(
                output_path,
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / "rollout.yaml",
            )

    def test_directory_output_uses_default_output_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "out"
            output_dir.mkdir()
            input_path = Path(tmpdir) / "rollout.jsonl"
            codex_home = Path(tmpdir) / ".codex"

            output_path = resolve_output_path(output_dir, input_path, codex_home, "yaml", "abc")

            self.assertEqual(output_path, (output_dir / "abc.yaml").resolve())

    def test_missing_input_exits_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_input = Path(tmpdir) / "missing.jsonl"
            output_path = Path(tmpdir) / "missing.yaml"

            with self.assertRaises(SystemExit) as raised:
                main([str(missing_input), "-o", str(output_path)])

            self.assertEqual(str(raised.exception), f"Input file not found: {missing_input}")
            self.assertFalse(output_path.exists())

    def test_session_id_input_converts_default_output_under_codex_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "019dd5ce-19e1-78c3-9313-325228ddd983"
            input_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(input_path, [{"type": "session_meta", "payload": {"id": session_id}}])

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main([session_id, "--codex-home", str(codex_home)])

            output_path = (
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / f"{session_id}.yaml"
            )
            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            self.assertIn(str(output_path), buffer.getvalue())

    def test_session_id_input_can_write_to_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            output_dir = Path(tmpdir) / "out"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            output_dir.mkdir()
            session_id = "019dd5ce-19e1-78c3-9313-325228ddd983"
            input_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(input_path, [{"type": "session_meta", "payload": {"id": session_id}}])

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main([session_id, "-o", str(output_dir), "--codex-home", str(codex_home)])

            output_path = output_dir / f"{session_id}.yaml"
            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            self.assertIn(str(output_path), buffer.getvalue())

    def test_conversion_rejects_positional_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.yaml"
            write_jsonl(input_path, [{"type": "session_meta", "payload": {"id": "abc"}}])

            with redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main([str(input_path), str(output_path)])

            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output_path.exists())

    def test_md_flag_converts_to_markdown_without_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "019dd5ce-19e1-78c3-9313-325228ddd983"
            input_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                input_path,
                [
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "hello",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "shell_command",
                            "arguments": '{"command":"echo hello"}',
                            "call_id": "call_1",
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "very long output",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["--md", session_id, "--codex-home", str(codex_home)])

            output_path = (
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / f"{session_id}.md"
            )
            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            output = output_path.read_text(encoding="utf-8")
            self.assertIn("# User:", output)
            self.assertIn("**Tool call:** `shell_command`", output)
            self.assertIn("Command preview:", output)
            self.assertIn("echo hello", output)
            self.assertIn("**Tool output:** `shell_command`", output)
            self.assertNotIn("very long output", output)
            self.assertIn(str(output_path), buffer.getvalue())

    def test_md_timing_flags_are_wired_to_markdown_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "shell_command",
                            "arguments": '{"command":"echo hello"}',
                            "call_id": "call_1",
                        },
                    },
                    {
                        "timestamp": "2026-04-26T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call_1",
                            "output": "hello",
                        },
                    },
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        str(input_path),
                        "--md",
                        "--timestamps",
                        "--tool-duration-threshold",
                        "0",
                        "-o",
                        str(output_path),
                    ]
                )

            output = output_path.read_text(encoding="utf-8")
            self.assertEqual(result, 0)
            self.assertIn("# Codex |", output)
            self.assertIn("Duration: `1s`", output)

    def test_timestamps_flag_requires_markdown_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            write_jsonl(input_path, [{"timestamp": "2026-04-26T00:00:00Z"}])

            with self.assertRaises(SystemExit) as raised:
                main([str(input_path), "--timestamps"])

            self.assertEqual(
                str(raised.exception),
                "--timestamps is only supported for Markdown output",
            )

    def test_yaml_flag_converts_to_yaml_without_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "019dd5ce-19e1-78c3-9313-325228ddd983"
            input_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(input_path, [{"type": "session_meta", "payload": {"id": session_id}}])

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["--yaml", session_id, "--codex-home", str(codex_home)])

            output_path = (
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / f"{session_id}.yaml"
            )
            self.assertEqual(result, 0)
            self.assertTrue(output_path.exists())
            self.assertIn("session_meta", output_path.read_text(encoding="utf-8"))
            self.assertIn(str(output_path), buffer.getvalue())

    def test_conversion_accepts_latest_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            older_path = sessions_day / "rollout-2026-04-30T18-20-39-old.jsonl"
            newer_path = sessions_day / "rollout-2026-04-30T19-20-39-new.jsonl"
            write_jsonl(
                older_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": "old"},
                    }
                ],
            )
            write_jsonl(
                newer_path,
                [
                    {
                        "timestamp": "2026-04-30T19:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": "new"},
                    },
                    {
                        "timestamp": "2026-04-30T19:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "latest session body",
                        },
                    },
                ],
            )
            os.utime(older_path, (1_800_000_100, 1_800_000_100))
            os.utime(newer_path, (1_800_000_000, 1_800_000_000))

            with redirect_stdout(StringIO()):
                result = main(["latest", "--md", "--codex-home", str(codex_home)])

            output_path = (
                codex_home
                / "tmp"
                / "sessions"
                / "2026"
                / "04"
                / "30"
                / "rollout-2026-04-30T19-20-39-new.md"
            )
            self.assertEqual(result, 0)
            self.assertIn("latest session body", output_path.read_text(encoding="utf-8"))

    def test_conversion_accepts_exact_session_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "019dd5ce-19e1-78c3-9313-325228ddd983"
            input_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                input_path,
                [
                    {"type": "session_meta", "payload": {"id": session_id}},
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "title resolved body",
                        },
                    },
                ],
            )
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Exact conversion title"}],
            )

            with redirect_stdout(StringIO()):
                result = main(["Exact conversion title", "--md", "--codex-home", str(codex_home)])

            output_path = (
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / f"{session_id}.md"
            )
            self.assertEqual(result, 0)
            self.assertIn("title resolved body", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
