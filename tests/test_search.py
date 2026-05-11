import re
import unittest

from codex_sessions_converter.search import (
    SearchOptions,
    compile_search_pattern,
    match_spans,
    search_matching_lines,
)


class SearchTests(unittest.TestCase):
    def test_compile_search_pattern_escapes_literal_pattern(self) -> None:
        pattern = compile_search_pattern(
            SearchOptions(
                pattern="a.b",
                regex=False,
                ignore_case=False,
                line_width=80,
                max_lines_per_session=5,
                include_metadata=False,
                include_tools=False,
                color="auto",
                redaction="...",
            )
        )

        self.assertIsNotNone(pattern.search("a.b"))
        self.assertIsNone(pattern.search("axb"))

    def test_compile_search_pattern_supports_case_insensitive_regex(self) -> None:
        pattern = compile_search_pattern(
            SearchOptions(
                pattern=r"copy-as-markdown|dadata-sdk",
                regex=True,
                ignore_case=True,
                line_width=80,
                max_lines_per_session=5,
                include_metadata=False,
                include_tools=False,
                color="auto",
                redaction="...",
            )
        )

        self.assertIsNotNone(pattern.search("COPY-AS-MARKDOWN"))

    def test_compile_search_pattern_rejects_invalid_regex(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid regex pattern"):
            compile_search_pattern(
                SearchOptions(
                    pattern="[",
                    regex=True,
                    ignore_case=False,
                    line_width=80,
                    max_lines_per_session=5,
                    include_metadata=False,
                    include_tools=False,
                    color="auto",
                    redaction="...",
                )
            )

    def test_match_spans_ignores_zero_width_matches(self) -> None:
        self.assertEqual(match_spans("abc", re.compile(r"(?=b)")), ())

    def test_search_matching_lines_truncates_single_match_with_label_prefix(self) -> None:
        pattern = re.compile("copy-as-markdown")
        line = f"User: {'a' * 80} copy-as-markdown {'b' * 80}"

        (result,) = search_matching_lines([line], pattern, 80)

        self.assertEqual(result.occurrence_count, 1)
        self.assertTrue(result.text.startswith("User: "))
        self.assertIn("copy-as-markdown", result.text)
        self.assertLessEqual(len(result.text), 80)

    def test_search_matching_lines_keeps_two_distant_matches_when_possible(self) -> None:
        pattern = re.compile("copy-as-markdown")
        line = f"{'a' * 80} copy-as-markdown {'b' * 80} copy-as-markdown {'c' * 80}"

        (result,) = search_matching_lines([line], pattern, 140)

        self.assertEqual(result.occurrence_count, 2)
        self.assertEqual(result.text.count("copy-as-markdown"), 2)
        self.assertLessEqual(len(result.text), 140)

    def test_search_matching_lines_summarizes_extra_matches_on_one_long_line(self) -> None:
        pattern = re.compile("copy-as-markdown")
        line = (
            "first useful context before copy-as-markdown "
            + ("middle " * 20)
            + "second useful context before copy-as-markdown "
            + ("more " * 20)
            + "third copy-as-markdown"
        )

        (result,) = search_matching_lines([line], pattern, 110)

        self.assertEqual(result.occurrence_count, 3)
        self.assertEqual(result.text.count("copy-as-markdown"), 1)
        self.assertIn("(+2 more on line)", result.text)


if __name__ == "__main__":
    unittest.main()
