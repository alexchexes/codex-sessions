import base64
import tempfile
import unittest
from pathlib import Path

from codex_sessions.formats.markdown.images import (
    MarkdownImageHandler,
    describe_data_image,
    image_extension,
    markdown_code_span,
    markdown_relative_link,
    parse_data_image_url,
)
from codex_sessions.sessions.message_content import is_image_content_item, is_image_wrapper_text


class MarkdownImageTests(unittest.TestCase):
    def test_parse_data_image_url_normalizes_media_type(self) -> None:
        parsed = parse_data_image_url("data:image/PNG;base64,abcd")

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.media_type, "image/png")
        self.assertEqual(parsed.encoded_data, "abcd")
        self.assertIsNone(parse_data_image_url("https://example.com/image.png"))

    def test_describe_data_image_includes_truncated_prefix_and_source(self) -> None:
        parsed = parse_data_image_url("data:image/png;base64," + ("a" * 40))
        assert parsed is not None

        description = describe_data_image(parsed, "`rollout.jsonl:1`")

        self.assertIn("image/png data URL", description)
        self.assertIn("40 base64 chars truncated", description)
        self.assertIn("source `rollout.jsonl:1`", description)
        self.assertIn("base64 prefix `aaaaaaaaaaaaaaaaaaaaaaaa...`", description)

    def test_markdown_image_handler_extracts_and_reuses_data_image(self) -> None:
        image_bytes = b"fake png bytes"
        image_url = f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "session.md"
            handler = MarkdownImageHandler("extract", markdown_path, Path(tmpdir) / "rollout.jsonl")

            first_link = handler.render_image(image_url, "input image")
            second_link = handler.render_image(image_url, "input image")

            image_files = list((Path(tmpdir) / "session_assets").glob("image-*.png"))
            extracted_bytes = image_files[0].read_bytes()

        self.assertEqual(first_link, second_link)
        self.assertEqual(len(image_files), 1)
        self.assertEqual(extracted_bytes, image_bytes)
        self.assertIn("![input image](session_assets/image-", first_link)

    def test_markdown_image_handler_truncates_and_inlines(self) -> None:
        image_url = "data:image/png;base64," + ("a" * 40)
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "session.md"
            input_path = Path(tmpdir) / "rollout.jsonl"
            truncate_handler = MarkdownImageHandler("truncate", markdown_path, input_path)
            truncate_handler.set_source_line(7)
            inline_handler = MarkdownImageHandler("inline", markdown_path, input_path)
            inline_handler.set_source_line(7)

            truncated = truncate_handler.render_image(image_url, "input image")
            inlined = inline_handler.render_image(image_url, "input image")

        self.assertIn("source `", truncated)
        self.assertIn(":7`", truncated)
        self.assertIn("[//]: # (Inline image;", inlined)
        self.assertIn("--md-images truncate", inlined)
        self.assertIn(f"![input image]({image_url})", inlined)

    def test_markdown_image_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.assertEqual(
                markdown_relative_link(root / "assets" / "image.png", root / "session.md"),
                "assets/image.png",
            )

        self.assertEqual(markdown_code_span("plain"), "`plain`")
        self.assertEqual(markdown_code_span("has ` tick"), "`` has ` tick ``")
        self.assertEqual(image_extension("image/svg+xml"), "svg")
        self.assertEqual(image_extension("image/x-custom+thing"), "xcustom")
        self.assertTrue(is_image_content_item({"type": "input_image", "image_url": "x"}))
        self.assertTrue(is_image_content_item({"image_url": "x"}))
        self.assertFalse(is_image_content_item({"type": "input_text", "text": "x"}))
        self.assertTrue(is_image_wrapper_text(" <image> "))
        self.assertFalse(is_image_wrapper_text("not image"))


if __name__ == "__main__":
    unittest.main()
