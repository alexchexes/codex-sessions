import tempfile
import unittest
from pathlib import Path

from codex_sessions.formats.markdown.images import MarkdownImageHandler
from codex_sessions.formats.markdown.message_content import content_to_text


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


if __name__ == "__main__":
    unittest.main()
