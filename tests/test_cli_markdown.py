import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli_args import (  # noqa: E402
    parse_duration_arg_seconds,
    parse_markdown_include,
    resolve_markdown_tool_mode,
)
from codex_sessions.formats.markdown.output import (  # noqa: E402
    MarkdownOptions,
    convert_jsonl_to_markdown,
    render_reasoning,
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


class CliMarkdownTests(unittest.TestCase):
    def test_markdown_conversion_ignores_incomplete_final_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            input_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "complete record",
                        },
                    },
                    ensure_ascii=False,
                )
                + '\n{"timestamp": "2026-04-26T00:00:01Z", "type":',
                encoding="utf-8",
            )

            count = convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="names",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertEqual(count, 3)
            self.assertIn("complete record", output)
            self.assertIn("# First Record:", output)
            self.assertIn("# Latest Record:", output)

    def test_markdown_metadata_table_escapes_pipes_and_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "turn_context",
                        "payload": {"note": "a|b\nc"},
                    }
                ],
            )

            convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=True,
                    include_raw=False,
                    redaction="...",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertIn("a\\|b<br>c", output)

    def test_tool_mode_auto_follows_include_preset(self) -> None:
        self.assertEqual(resolve_markdown_tool_mode({"tools"}, "auto"), "smart")
        self.assertEqual(resolve_markdown_tool_mode(set(), "auto"), "none")
        self.assertEqual(resolve_markdown_tool_mode(set(), "names"), "names")

    def test_encrypted_reasoning_renders_as_single_line(self) -> None:
        self.assertEqual(
            render_reasoning({"type": "reasoning", "encrypted_content": "secret"}, "..."),
            "**Reasoning (encrypted_content) ...**",
        )

    def test_include_modifiers(self) -> None:
        self.assertEqual(parse_markdown_include("default,-tools"), set())
        self.assertEqual(parse_markdown_include("dialogue,+metadata"), {"metadata"})
        self.assertEqual(parse_markdown_include("dialogue,+reasoning"), {"reasoning"})
        self.assertNotIn("reasoning", parse_markdown_include("default"))
        self.assertIn("reasoning", parse_markdown_include("full"))
        self.assertNotIn("reasoning", parse_markdown_include("full,-reasoning"))

    def test_markdown_reasoning_is_opt_in_and_never_falls_through_to_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            without_reasoning_path = Path(tmpdir) / "without-reasoning.md"
            with_reasoning_path = Path(tmpdir) / "with-reasoning.md"
            write_jsonl(
                input_path,
                [
                    import_user_message("visible message", "2026-04-26T00:00:00Z"),
                    {
                        "timestamp": "2026-04-26T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "reasoning",
                            "content": [{"type": "input_text", "text": "readable reasoning"}],
                        },
                    },
                    {
                        "timestamp": "2026-04-26T00:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "reasoning",
                            "encrypted_content": "secret",
                        },
                    },
                ],
            )

            convert_jsonl_to_markdown(
                input_path,
                without_reasoning_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=True,
                    redaction="...",
                    include_reasoning=False,
                ),
            )
            convert_jsonl_to_markdown(
                input_path,
                with_reasoning_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                    include_reasoning=True,
                ),
            )

            without_reasoning = without_reasoning_path.read_text(encoding="utf-8")
            with_reasoning = with_reasoning_path.read_text(encoding="utf-8")
            self.assertIn("visible message", without_reasoning)
            self.assertNotIn("readable reasoning", without_reasoning)
            self.assertNotIn("encrypted_content", without_reasoning)
            self.assertIn("readable reasoning", with_reasoning)
            self.assertIn("**Reasoning (encrypted_content) ...**", with_reasoning)

    def test_parse_duration_arg_seconds_accepts_common_units(self) -> None:
        self.assertEqual(parse_duration_arg_seconds("0"), 0)
        self.assertEqual(parse_duration_arg_seconds("30s"), 30)
        self.assertEqual(parse_duration_arg_seconds("5m"), 300)
        self.assertEqual(parse_duration_arg_seconds("4h"), 14400)
        self.assertEqual(parse_duration_arg_seconds("250ms"), 0.25)

    def test_markdown_timestamps_add_times_to_section_headings(self) -> None:
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
                            "type": "message",
                            "role": "user",
                            "content": "hello",
                        },
                    }
                ],
            )

            convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                    timestamps=True,
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertRegex(output, r"# User \| .+ \(UTC[+-]\d\d:\d\d\):")
            self.assertNotIn("# First Record", output)
            self.assertNotIn("# Latest Record", output)
            self.assertIn("hello", output)

    def test_markdown_inserts_long_gap_markers_between_rendered_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            write_jsonl(
                input_path,
                [
                    import_user_message("first", "2026-04-26T00:00:00Z"),
                    {
                        "timestamp": "2026-04-26T01:00:00Z",
                        "type": "event_msg",
                        "payload": {"type": "token_count", "total": 1},
                    },
                    import_user_message("second", "2026-04-26T05:00:00Z"),
                ],
            )

            convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                    gap_threshold_seconds=4 * 60 * 60,
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertIn("# Time Gap:", output)
            self.assertIn("`5h` elapsed since previous rendered event.", output)
            self.assertNotIn("1h", output)

    def test_markdown_timestamps_keep_gap_markers_without_boundary_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            write_jsonl(
                input_path,
                [
                    import_user_message("first", "2026-04-26T00:00:00Z"),
                    import_user_message("second", "2026-04-26T05:00:00Z"),
                ],
            )

            convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                    timestamps=True,
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertRegex(output, r"# Time Gap \| .+ \(UTC[+-]\d\d:\d\d\):")
            self.assertIn("`5h` elapsed since previous rendered event.", output)
            self.assertNotIn("# First Record", output)
            self.assertNotIn("# Latest Record", output)


if __name__ == "__main__":
    unittest.main()
