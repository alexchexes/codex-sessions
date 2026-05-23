import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codex_sessions.sessions.display import (
    NO_SESSION_INDEX_ENTRY,
    SessionDisplayInfo,
    format_indexed_session_line,
    format_session_timestamps,
    format_unindexed_session_line,
    session_info_for_search,
    session_title_for_search,
    session_title_match_spans,
)
from codex_sessions.sessions.files import SessionFile
from codex_sessions.sessions.index import SessionIndexEntry


class SessionDisplayTests(unittest.TestCase):
    def test_format_session_timestamps_handles_full_and_partial_times(self) -> None:
        started_at = datetime(2026, 4, 30, 18, 20, tzinfo=timezone.utc)
        ended_at = datetime(2026, 4, 30, 19, 5, tzinfo=timezone.utc)

        self.assertIn(
            " - ",
            format_session_timestamps(
                SessionFile(
                    path=Path("rollout.jsonl"),
                    relative_path="rollout.jsonl",
                    session_id="id",
                    started_at=started_at,
                    ended_at=ended_at,
                )
            ),
        )
        self.assertNotIn(
            " - ",
            format_session_timestamps(
                SessionFile(
                    path=Path("rollout.jsonl"),
                    relative_path="rollout.jsonl",
                    session_id="id",
                    started_at=started_at,
                    ended_at=None,
                )
            ),
        )

    def test_format_indexed_and_unindexed_session_lines(self) -> None:
        session_file = SessionFile(
            path=Path("rollout.jsonl"),
            relative_path="2026/04/30/rollout.jsonl",
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            started_at=None,
            ended_at=None,
        )
        entry = SessionIndexEntry(
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            thread_name="Indexed title",
            updated_at=None,
        )

        self.assertEqual(
            format_indexed_session_line(entry, session_file),
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa - Indexed title",
        )
        self.assertEqual(
            format_unindexed_session_line(session_file, "Inferred title"),
            (f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa - Inferred title - {NO_SESSION_INDEX_ENTRY}"),
        )
        self.assertEqual(
            format_unindexed_session_line(session_file, None),
            f"2026/04/30/rollout.jsonl - {NO_SESSION_INDEX_ENTRY}",
        )

    def test_session_info_and_title_for_search_prefer_index_entry(self) -> None:
        session_file = SessionFile(
            path=Path("rollout.jsonl"),
            relative_path="rollout.jsonl",
            session_id="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
            started_at=None,
            ended_at=None,
        )
        entry = SessionIndexEntry(
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            thread_name="Indexed title",
            updated_at=None,
        )

        self.assertEqual(
            session_info_for_search(
                session_file,
                {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": entry},
                "Inferred title",
            ),
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa - Indexed title",
        )
        self.assertEqual(
            session_title_for_search(
                session_file,
                {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": entry},
                "Inferred title",
            ),
            "Indexed title",
        )

    def test_session_title_match_spans_stay_relative_to_title(self) -> None:
        session_info = SessionDisplayInfo(
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Fix Search",
        )

        spans = session_title_match_spans(session_info, re.compile("search", re.I))

        self.assertEqual(spans, ((4, 10),))
        self.assertEqual(
            session_title_match_spans(
                SessionDisplayInfo(session_id="id", title=None), re.compile("x")
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
