import tempfile
import unittest
from pathlib import Path

from codex_sessions.formats.markdown.formatting import (
    fenced_block,
    flatten_table_rows,
    parse_json_maybe,
    render_json_block_content,
    render_markdown_table,
    render_markdown_table_value,
)
from codex_sessions.formats.markdown.images import MarkdownImageHandler


class MarkdownFormattingTests(unittest.TestCase):
    def test_render_json_block_content_pretty_prints_json(self) -> None:
        self.assertEqual(render_json_block_content({"a": 1}), '{\n  "a": 1\n}')

    def test_render_markdown_table_value_escapes_markdown_table_syntax(self) -> None:
        self.assertEqual(render_markdown_table_value("a|b\nc"), r"a\|b<br>c")
        self.assertEqual(render_markdown_table_value(None), "null")
        self.assertEqual(render_markdown_table_value(True), "true")
        self.assertEqual(render_markdown_table_value({"a": ["b|c"]}), r'{"a":["b\|c"]}')

    def test_flatten_table_rows_preserves_nested_paths_and_empty_containers(self) -> None:
        rows = flatten_table_rows({"a": {"b": 1}, "empty": [], "items": [{"x": 2}]})

        self.assertEqual(
            rows,
            [
                ("a.b", 1),
                ("empty", []),
                ("items[0].x", 2),
            ],
        )

    def test_render_markdown_table_renders_flattened_rows(self) -> None:
        self.assertEqual(
            render_markdown_table({"a": {"b": 1}, "c": "x|y"}),
            "| Field | Value |\n| --- | --- |\n| a.b | 1 |\n| c | x\\|y |",
        )

    def test_fenced_block_uses_longer_fence_when_content_contains_backticks(self) -> None:
        self.assertEqual(fenced_block("plain", "text"), "```text\nplain\n```")
        self.assertEqual(fenced_block("a\n```", "json"), "````json\na\n```\n````")

    def test_parse_json_maybe_detects_json_and_text(self) -> None:
        self.assertEqual(parse_json_maybe('{"a":1}'), ('{\n  "a": 1\n}', "json"))
        self.assertEqual(parse_json_maybe("{not json}"), ("{not json}", "text"))
        self.assertEqual(parse_json_maybe("plain text"), ("plain text", "text"))
        self.assertEqual(parse_json_maybe({"a": 1}), ('{\n  "a": 1\n}', "json"))

    def test_parse_json_maybe_applies_image_handler(self) -> None:
        image_url = "data:image/png;base64,abcd"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handler = MarkdownImageHandler("truncate", root / "session.md", root / "rollout.jsonl")

            body, language = parse_json_maybe({"image_url": image_url}, handler)

        self.assertEqual(language, "json")
        self.assertIn("image/png data URL", body)
        self.assertIn("base64 prefix `abcd`", body)


if __name__ == "__main__":
    unittest.main()
