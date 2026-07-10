import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from codex_sessions.sessions.cache import (
    SESSION_CACHE_VERSION,
    cached_file_fingerprint,
    cached_session_metadata,
    file_fingerprint_from_session_cache,
    prune_missing_session_cache_entries,
    read_session_cache,
    session_cache_entry_from_document,
    session_cache_key,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.documents import SearchDocument
from codex_sessions.sessions.rollout import FileFingerprint


class SessionCacheTests(unittest.TestCase):
    def test_session_cache_path_uses_codex_cache_directory(self) -> None:
        self.assertEqual(
            session_cache_path(Path("/tmp/codex")).as_posix(),
            "/tmp/codex/cache/codex-sessions/sessions-v3.json",
        )

    def test_read_session_cache_returns_entries_for_current_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "sessions.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": SESSION_CACHE_VERSION,
                        "entries": {"key": {"path": "value"}},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(read_session_cache(cache_path), {"key": {"path": "value"}})

    def test_read_session_cache_ignores_invalid_or_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "sessions.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "version": SESSION_CACHE_VERSION - 1,
                        "entries": {"stale": {}},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(read_session_cache(cache_path), {})

            cache_path.write_text("not json", encoding="utf-8")
            self.assertEqual(read_session_cache(cache_path), {})

            self.assertEqual(read_session_cache(Path(tmpdir) / "missing.json"), {})

    def test_write_session_cache_round_trips_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "nested" / "sessions.json"
            entries = {"key": {"path": "value"}}

            write_session_cache(cache_path, entries)

            self.assertEqual(read_session_cache(cache_path), entries)

    def test_session_cache_entry_round_trips_metadata_and_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            rollout_path.write_text("content", encoding="utf-8")
            stat_result = rollout_path.stat()
            fingerprint = FileFingerprint(size=7, sha256="a" * 64)
            document = SearchDocument(
                session_id="11111111-1111-1111-1111-111111111111",
                thread_name="Cached title",
                started_at=datetime(2026, 4, 30, 18, 20, 39, tzinfo=timezone.utc),
                ended_at=datetime(2026, 4, 30, 18, 21, 39, tzinfo=timezone.utc),
                last_activity_at=datetime(2026, 4, 30, 18, 21, 30, tzinfo=timezone.utc),
                visible_lines=(),
                metadata_lines=(),
                tool_input_lines=(),
                tool_output_lines=(),
                session_id_is_canonical=True,
            )

            entry = session_cache_entry_from_document(
                rollout_path, stat_result, document, fingerprint=fingerprint
            )
            cached_entry = cached_session_metadata(entry, rollout_path, stat_result)

            self.assertIsNotNone(cached_entry)
            assert cached_entry is not None
            self.assertEqual(cached_entry.session_id, document.session_id)
            self.assertEqual(cached_entry.thread_name, document.thread_name)
            self.assertEqual(cached_entry.started_at, document.started_at)
            self.assertEqual(cached_entry.last_activity_at, document.last_activity_at)
            self.assertTrue(cached_entry.timestamps_scanned)
            self.assertTrue(cached_entry.session_id_is_canonical)
            self.assertEqual(cached_file_fingerprint(entry, rollout_path, stat_result), fingerprint)
            self.assertEqual(session_cache_key(rollout_path), session_cache_key(rollout_path))

    def test_cached_session_metadata_rejects_mismatched_or_invalid_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            rollout_path.write_text("content", encoding="utf-8")
            stat_result = rollout_path.stat()
            entry = {
                "path": str(rollout_path.resolve()),
                "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
                "sha256": None,
                "session_id_is_canonical": False,
                "identity_warning": None,
                "identity_status": None,
            }

            self.assertIsNone(
                cached_session_metadata(
                    {**entry, "size": stat_result.st_size + 1}, rollout_path, stat_result
                )
            )
            self.assertIsNone(
                cached_session_metadata({**entry, "session_id": 1}, rollout_path, stat_result)
            )
            self.assertIsNone(
                cached_session_metadata({**entry, "sha256": "not-a-sha"}, rollout_path, stat_result)
            )
            self.assertIsNone(
                cached_session_metadata(
                    {**entry, "timestamps_scanned": "yes"}, rollout_path, stat_result
                )
            )

            cached_entry = cached_session_metadata(entry, rollout_path, stat_result)
            self.assertIsNotNone(cached_entry)
            assert cached_entry is not None
            self.assertFalse(cached_entry.timestamps_scanned)

    def test_file_fingerprint_from_session_cache_reuses_cached_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            rollout_path.write_text("content", encoding="utf-8")
            stat_result = rollout_path.stat()
            expected = FileFingerprint(size=stat_result.st_size, sha256="b" * 64)
            entries = {
                session_cache_key(rollout_path): {
                    "path": str(rollout_path.resolve()),
                    "size": stat_result.st_size,
                    "mtime_ns": stat_result.st_mtime_ns,
                    "sha256": expected.sha256,
                    "session_id_is_canonical": False,
                    "identity_warning": None,
                    "identity_status": None,
                }
            }

            with patch(
                "codex_sessions.sessions.cache.file_fingerprint",
                side_effect=AssertionError("cached fingerprint should be reused"),
            ):
                fingerprint, _, updated = file_fingerprint_from_session_cache(rollout_path, entries)

            self.assertEqual(fingerprint, expected)
            self.assertFalse(updated)

    def test_file_fingerprint_from_session_cache_hashes_and_updates_missing_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rollout_path = Path(tmpdir) / "rollout.jsonl"
            rollout_path.write_bytes(b"hello")
            entries: dict[str, object] = {}

            fingerprint, stat_result, updated = file_fingerprint_from_session_cache(
                rollout_path, entries
            )

            self.assertEqual(fingerprint.size, 5)
            self.assertTrue(updated)
            cached_fingerprint = cached_file_fingerprint(
                entries[session_cache_key(rollout_path)], rollout_path, stat_result
            )
            self.assertEqual(cached_fingerprint, fingerprint)

    def test_prune_missing_session_cache_entries_removes_invalid_and_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = Path(tmpdir) / "existing.jsonl"
            existing_path.write_text("content", encoding="utf-8")
            entries = {
                "existing": {"path": str(existing_path)},
                "missing": {"path": str(Path(tmpdir) / "missing.jsonl")},
                "invalid": [],
            }

            removed = prune_missing_session_cache_entries(entries)

            self.assertTrue(removed)
            self.assertEqual(entries, {"existing": {"path": str(existing_path)}})


if __name__ == "__main__":
    unittest.main()
