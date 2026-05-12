import tempfile
import unittest
from pathlib import Path

from codex_sessions.formats.markdown.images import MarkdownImageHandler
from codex_sessions.formats.markdown.message_content import (
    content_to_text,
    is_injected_user_context,
    searchable_user_message_text,
)


class MessageContentTests(unittest.TestCase):
    def test_content_to_text_returns_plain_string_and_json_for_structured_value(self) -> None:
        self.assertEqual(content_to_text("plain"), "plain")
        self.assertEqual(content_to_text({"a": 1}), '{\n  "a": 1\n}')

    def test_content_to_text_combines_text_and_image_items(self) -> None:
        image_url = "data:image/png;base64,abcd"

        self.assertEqual(
            content_to_text(
                [
                    {"type": "input_text", "text": "hello"},
                    {"type": "input_image", "image_url": image_url},
                    {"type": "local_image", "path": "C:/tmp/image.png"},
                ]
            ),
            f"hello\n\n[input image: {image_url}]\n\n[local image: C:/tmp/image.png]",
        )

    def test_content_to_text_skips_image_wrapper_when_image_item_exists(self) -> None:
        image_url = "data:image/png;base64,abcd"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handler = MarkdownImageHandler("truncate", root / "session.md", root / "rollout.jsonl")

            rendered = content_to_text(
                [
                    {"type": "input_text", "text": "<image>"},
                    {"type": "input_image", "image_url": image_url},
                ],
                handler,
            )

        self.assertNotIn("<image>", rendered)
        self.assertIn("image/png data URL", rendered)

    def test_searchable_user_message_text_filters_injected_context(self) -> None:
        self.assertTrue(is_injected_user_context("# AGENTS.md instructions\n..."))
        self.assertEqual(searchable_user_message_text("<environment_context>\n..."), "")
        self.assertEqual(searchable_user_message_text("# AGENTS.md instructions\n..."), "")

    def test_searchable_user_message_text_extracts_actual_ide_request(self) -> None:
        self.assertEqual(
            searchable_user_message_text(
                "# Context from my IDE setup:\n"
                "## Open tabs:\n"
                "- file.py\n"
                "## My request for Codex:\n"
                "please fix this\n"
            ),
            "please fix this",
        )
        self.assertEqual(
            searchable_user_message_text("# Context from my IDE setup:\nno request"), ""
        )


if __name__ == "__main__":
    unittest.main()
