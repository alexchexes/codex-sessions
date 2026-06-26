import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.formats.markdown.output import (  # noqa: E402
    MarkdownOptions,
    convert_jsonl_to_markdown,
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


class CliMarkdownImageTests(unittest.TestCase):
    def test_markdown_truncates_data_images_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            encoded_image = base64.b64encode(b"fake png bytes" * 10).decode("ascii")
            expected_prefix = f"{encoded_image[:24]}..."
            image_url = f"data:image/png;base64,{encoded_image}"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "see this"},
                                {"type": "input_text", "text": "<image>"},
                                {"type": "input_image", "image_url": image_url},
                                {"type": "input_text", "text": "</image>"},
                            ],
                        },
                    }
                ],
            )

            count = convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertEqual(count, 3)
            self.assertIn("see this", output)
            self.assertIn("[input image: image/png data URL;", output)
            self.assertIn("base64 chars truncated", output)
            self.assertIn(f"source `{input_path}:1`", output)
            self.assertIn(f"base64 prefix `{expected_prefix}`", output)
            self.assertNotIn(encoded_image, output)
            self.assertNotIn("<image>", output)
            self.assertNotIn("</image>", output)

    def test_markdown_extracts_data_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            image_bytes = b"fake png bytes"
            encoded_image = base64.b64encode(image_bytes).decode("ascii")
            image_url = f"data:image/png;base64,{encoded_image}"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "<image>"},
                                {"type": "input_image", "image_url": image_url},
                                {"type": "input_text", "text": "</image>"},
                            ],
                        },
                    }
                ],
            )

            count = convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=False,
                    redaction="...",
                    image_mode="extract",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            image_files = list((Path(tmpdir) / "rollout_assets").glob("image-*.png"))
            self.assertEqual(count, 3)
            self.assertEqual(len(image_files), 1)
            self.assertEqual(image_files[0].read_bytes(), image_bytes)
            self.assertIn("![input image](rollout_assets/image-", output)
            self.assertNotIn(encoded_image, output)
            self.assertNotIn("<image>", output)
            self.assertNotIn("</image>", output)

    def test_markdown_inline_data_images_adds_hidden_extraction_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            encoded_image = base64.b64encode(b"fake png bytes" * 10).decode("ascii")
            image_url = f"data:image/png;base64,{encoded_image}"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": image_url},
                            ],
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
                    image_mode="inline",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertIn("[//]: # (Inline image;", output)
            self.assertIn("--md-images truncate", output)
            self.assertIn("--md-images extract", output)
            self.assertNotIn("To keep the Markdown small", output)
            self.assertNotIn("&#45;&#45;", output)
            self.assertIn(f"Source: {input_path}:1.", output)
            self.assertIn(f"![input image]({image_url})", output)

    def test_markdown_keeps_literal_image_tags_without_image_item(self) -> None:
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
                            "content": [
                                {"type": "input_text", "text": "<image>"},
                            ],
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
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertIn("<image>", output)

    def test_markdown_full_raw_truncates_data_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            output_path = Path(tmpdir) / "rollout.md"
            encoded_image = base64.b64encode(b"fake png bytes" * 10).decode("ascii")
            expected_prefix = f"{encoded_image[:24]}..."
            image_url = f"data:image/png;base64,{encoded_image}"
            write_jsonl(
                input_path,
                [
                    {
                        "timestamp": "2026-04-26T00:00:00Z",
                        "type": "unknown",
                        "payload": {"image_url": image_url},
                    }
                ],
            )

            count = convert_jsonl_to_markdown(
                input_path,
                output_path,
                MarkdownOptions(
                    tool_mode="none",
                    tool_preview_chars=80,
                    include_metadata=False,
                    include_raw=True,
                    redaction="...",
                ),
            )

            output = output_path.read_text(encoding="utf-8")
            self.assertEqual(count, 3)
            self.assertIn("data:image/png;base64,image/png data URL;", output)
            self.assertIn("rollout.jsonl:1", output)
            self.assertIn(f"base64 prefix `{expected_prefix}`", output)
            self.assertNotIn(encoded_image, output)
