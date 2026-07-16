import unittest

from codex_sessions.formats.markdown.tools import (
    normalized_tool_short_name,
    parse_json_object_maybe,
    render_smart_tool_call_preview,
    render_tool_call,
    render_tool_output,
    tool_display_name,
    tool_name_is_included,
    truncate_preview,
)


class MarkdownToolsTests(unittest.TestCase):
    def test_parse_json_object_maybe_accepts_dict_and_json_object_strings(self) -> None:
        self.assertEqual(parse_json_object_maybe({"a": 1}), {"a": 1})
        self.assertEqual(parse_json_object_maybe('{"a":1}'), {"a": 1})
        self.assertIsNone(parse_json_object_maybe("[1, 2]"))
        self.assertIsNone(parse_json_object_maybe("{not json}"))

    def test_tool_name_helpers_normalize_known_legacy_prefixes(self) -> None:
        self.assertEqual(
            normalized_tool_short_name("mcp__playwright__browser_navigate"),
            "browser_navigate",
        )
        self.assertEqual(
            normalized_tool_short_name("functions.shell_command"),
            "shell_command",
        )
        self.assertEqual(
            tool_display_name({"namespace": "functions", "name": "shell_command"}),
            "functions.shell_command",
        )

    def test_tool_name_include_supports_exact_names_and_case_sensitive_globs(self) -> None:
        full_name = "mcp__ask_human.ask_human"

        self.assertTrue(tool_name_is_included(full_name, None))
        self.assertTrue(tool_name_is_included(full_name, frozenset({full_name})))
        self.assertTrue(tool_name_is_included(full_name, frozenset({"*ask_human*"})))
        self.assertTrue(tool_name_is_included(full_name, frozenset({"mcp__ask_*.ask_?????"})))
        self.assertFalse(tool_name_is_included(full_name, frozenset({"ask_human"})))
        self.assertFalse(tool_name_is_included(full_name, frozenset({"*ASK_HUMAN*"})))

    def test_truncate_preview_reports_omitted_character_count(self) -> None:
        self.assertEqual(truncate_preview("abc", 10), "abc")
        self.assertEqual(
            truncate_preview("abcdef", 3),
            "abc\n\n... [truncated, 3 characters omitted]",
        )

    def test_render_smart_tool_call_preview_extracts_shell_command(self) -> None:
        rendered = render_smart_tool_call_preview(
            "shell_command",
            '{"command":"echo hello","workdir":"D:/repo","timeout_ms":1000}',
            80,
        )

        assert rendered is not None
        self.assertIn("Command preview:", rendered)
        self.assertIn("echo hello", "\n".join(rendered))
        self.assertIn("Workdir: `D:/repo`", rendered)
        self.assertIn("Timeout ms: `1000`", rendered)

    def test_render_smart_tool_call_preview_extracts_ask_human_input(self) -> None:
        rendered = render_smart_tool_call_preview(
            "mcp__ask_human.ask_human",
            '{"question":"Continue?","context":"A decision is needed."}',
            80,
        )

        assert rendered is not None
        self.assertIn("Question: `Continue?`", rendered)
        self.assertIn("Context: `A decision is needed.`", rendered)

    def test_render_smart_tool_call_preview_returns_none_for_unknown_shape(self) -> None:
        self.assertIsNone(render_smart_tool_call_preview("future_tool", "plain", 80))

    def test_render_tool_call_names_and_preview_modes(self) -> None:
        payload = {
            "type": "function_call",
            "name": "shell_command",
            "arguments": '{"command":"echo hello"}',
            "call_id": "call_1",
        }

        names_rendered, tool_name = render_tool_call(payload, "names", 80)
        preview_rendered, _ = render_tool_call(payload, "preview", 20)

        self.assertEqual(tool_name, "shell_command")
        self.assertEqual(
            names_rendered,
            "**Tool call:** `shell_command`\nCall ID: `call_1`",
        )
        self.assertIn("Arguments preview:", preview_rendered)
        self.assertIn("truncated", preview_rendered)

    def test_render_tool_call_smart_mode_falls_back_to_name_for_unknown_tool(self) -> None:
        rendered, tool_name = render_tool_call(
            {"type": "function_call", "name": "future_tool", "arguments": "plain"},
            "smart",
            80,
        )

        self.assertEqual(tool_name, "future_tool")
        self.assertEqual(rendered, "**Tool call:** `future_tool`")

    def test_render_tool_output_names_preview_and_full_modes(self) -> None:
        payload = {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"ok":true,"message":"hello"}',
        }
        names = render_tool_output(payload, "names", 80, {"call_1": "shell_command"})
        preview = render_tool_output(payload, "preview", 20, {"call_1": "shell_command"})
        full = render_tool_output(payload, "full", 80, {"call_1": "shell_command"})

        self.assertEqual(
            names,
            "**Tool output:** `shell_command`\nCall ID: `call_1`",
        )
        self.assertIn("Output preview:", preview)
        self.assertIn("truncated", preview)
        self.assertIn('"ok": true', full)


if __name__ == "__main__":
    unittest.main()
