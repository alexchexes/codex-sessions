import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402
from codex_sessions.search.cache import search_cache_path  # noqa: E402
from codex_sessions.sessions.files import session_id_from_path  # noqa: E402


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    session_id = session_id_from_path(path)
    if session_id and (not records or records[0].get("type") != "session_meta"):
        records = [{"type": "session_meta", "payload": {"id": session_id}}, *records]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


class CliFindCacheTests(unittest.TestCase):
    def test_find_reuses_cached_search_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "cached needle",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                first_result = main(["find", "needle", "--codex-home", str(codex_home)])
            self.assertEqual(first_result, 0)
            self.assertTrue(search_cache_path(codex_home).exists())

            with patch(
                "codex_sessions.sessions.documents.iter_jsonl_objects",
                side_effect=AssertionError("cache should avoid reparsing rollout JSONL"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    second_result = main(["find", "needle", "--codex-home", str(codex_home)])

            self.assertEqual(second_result, 0)
            self.assertIn("cached needle", buffer.getvalue())

    def test_find_ignores_stale_search_cache_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "fresh needle",
                        },
                    }
                ],
            )
            cache_path = search_cache_path(codex_home)
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "stale": {
                                "path": str((sessions_day / "missing.jsonl").resolve()),
                                "visible_lines": ["Codex: stale needle"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["find", "needle", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("fresh needle", output)
            self.assertNotIn("stale needle", output)

    def test_find_invalidates_cache_when_rollout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
            session_path = sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            write_jsonl(
                session_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "old needle",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                first_result = main(["find", "needle", "--codex-home", str(codex_home)])
            self.assertEqual(first_result, 0)

            write_jsonl(
                session_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "new replacement text",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                old_result = main(["find", "needle", "--codex-home", str(codex_home)])
            buffer = StringIO()
            with redirect_stdout(buffer):
                new_result = main(["find", "replacement", "--codex-home", str(codex_home)])

            self.assertEqual(old_result, 1)
            self.assertEqual(new_result, 0)
            self.assertIn("new replacement text", buffer.getvalue())

    def test_find_no_cache_does_not_write_search_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "abababab-abab-abab-abab-abababababab"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "uncached needle",
                        },
                    }
                ],
            )

            with redirect_stdout(StringIO()):
                result = main(["find", "--no-cache", "needle", "--codex-home", str(codex_home)])

            self.assertEqual(result, 0)
            self.assertFalse(search_cache_path(codex_home).exists())


if __name__ == "__main__":
    unittest.main()
