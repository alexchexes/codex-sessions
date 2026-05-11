import unittest
from io import StringIO

from codex_sessions_converter.search import SearchLine
from codex_sessions_converter.search_output import (
    console_color_options,
    encode_for_output,
    text_with_highlights,
)


class SearchOutputTests(unittest.TestCase):
    def test_encode_for_output_escapes_unencodable_characters(self) -> None:
        self.assertEqual(encode_for_output("Thread \u2713", "cp1252"), r"Thread \u2713")
        self.assertEqual(encode_for_output("Thread \u2713", "utf-8"), "Thread \u2713")
        self.assertEqual(encode_for_output("Thread \u2713", None), "Thread \u2713")

    def test_text_with_highlights_preserves_text_and_styles_matches(self) -> None:
        rendered = text_with_highlights(
            SearchLine(text="Codex: hello needle", matches=((13, 19),), occurrence_count=1),
            "utf-8",
        )

        self.assertEqual(rendered.plain, "Codex: hello needle")
        self.assertIn("bright_red", str(rendered.spans[1].style))

    def test_console_color_options_respects_explicit_and_environment_flags(self) -> None:
        git_bash_env = {"TERM": "xterm-256color", "MSYSTEM": "MINGW64"}

        self.assertEqual(console_color_options("always", StringIO(), {}), (True, False))
        self.assertEqual(console_color_options("never", StringIO(), {}), (False, True))
        self.assertEqual(console_color_options("auto", StringIO(), {"NO_COLOR": "1"}), (None, True))
        self.assertEqual(
            console_color_options("auto", StringIO(), {"FORCE_COLOR": "1"}), (True, False)
        )
        self.assertIn(
            console_color_options("auto", StringIO(), git_bash_env), {(None, False), (True, False)}
        )


if __name__ == "__main__":
    unittest.main()
