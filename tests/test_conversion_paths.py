import argparse
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from codex_sessions.errors import CliError
from codex_sessions.sessions.cache import (
    read_session_cache,
    session_cache_entry,
    session_cache_key,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.files import SessionFileMetadata, SessionIdentity
from codex_sessions.sessions.paths import (
    default_output_path,
    infer_output_format,
    output_filename,
    resolve_conversion_input,
    resolve_output_path,
)
from codex_sessions.sessions.rollout import FileFingerprint


class ConversionPathsTests(unittest.TestCase):
    def test_infer_output_format_prefers_explicit_flags_and_output_suffix(self) -> None:
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=True, yaml=False, format=None, output=Path("out.yaml"))
            ),
            "md",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=True, format="md", output=Path("out.md"))
            ),
            "yaml",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=False, format="markdown", output=Path("out.yaml"))
            ),
            "md",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=False, format=None, output=Path("out.md"))
            ),
            "md",
        )

    def test_output_filename_uses_stem_and_jsonl_suffix(self) -> None:
        self.assertEqual(output_filename(Path("rollout.jsonl")), "rollout.yaml")
        self.assertEqual(output_filename(Path("rollout.jsonl"), "md"), "rollout.md")
        self.assertEqual(output_filename(Path("rollout.txt"), "yaml"), "rollout.txt.yaml")
        self.assertEqual(
            output_filename(Path("rollout.jsonl"), "yaml", "session-id"), "session-id.yaml"
        )

    def test_default_and_directory_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / ".codex"
            input_path = codex_home / "sessions" / "2026" / "04" / "30" / "rollout.jsonl"
            output_dir = root / "out"
            output_dir.mkdir()

            self.assertEqual(
                default_output_path(input_path, codex_home, "yaml"),
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / "rollout.yaml",
            )
            self.assertEqual(
                resolve_output_path(output_dir, input_path, codex_home, "md", "abc"),
                (output_dir / "abc.md").resolve(),
            )

    def test_resolve_conversion_input_reports_missing_file_without_stack_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.jsonl"

            with self.assertRaises(CliError) as raised:
                resolve_conversion_input(missing, Path(tmpdir) / ".codex")

        self.assertEqual(str(raised.exception), f"Input file not found: {missing}")

    def test_resolve_conversion_input_accepts_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-2026-05-28T10-00-00-older.jsonl"
            newer = sessions_day / "rollout-2026-05-28T11-00-00-newer.jsonl"
            write_jsonl(
                older,
                [
                    {
                        "timestamp": "2026-05-28T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "older"},
                    }
                ],
            )
            write_jsonl(
                newer,
                [
                    {
                        "timestamp": "2026-05-28T13:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "newer"},
                    }
                ],
            )
            os.utime(older, (1_800_000_100, 1_800_000_100))
            os.utime(newer, (1_800_000_000, 1_800_000_000))

            resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, newer.resolve())
        self.assertIsNone(resolved.output_stem)

    def test_resolve_conversion_input_latest_reuses_session_metadata_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-older.jsonl"
            newer = sessions_day / "rollout-newer.jsonl"
            write_jsonl(older, [{"type": "session_meta", "payload": {"id": "older"}}])
            write_jsonl(newer, [{"type": "session_meta", "payload": {"id": "newer"}}])
            write_session_cache(
                session_cache_path(codex_home),
                {
                    session_cache_key(older): session_cache_entry(
                        older,
                        older.stat(),
                        session_id="older",
                        started_at=utc_datetime(2026, 5, 28, 12, 0),
                        ended_at=utc_datetime(2026, 5, 28, 12, 0),
                    ),
                    session_cache_key(newer): session_cache_entry(
                        newer,
                        newer.stat(),
                        session_id="newer",
                        started_at=utc_datetime(2026, 5, 28, 13, 0),
                        ended_at=utc_datetime(2026, 5, 28, 13, 0),
                    ),
                },
            )

            with patch(
                "codex_sessions.sessions.paths.read_session_file_metadata",
                side_effect=AssertionError("cached metadata should be reused"),
            ):
                resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, newer.resolve())

    def test_resolve_conversion_input_latest_ignores_stale_session_metadata_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-older.jsonl"
            newer = sessions_day / "rollout-newer.jsonl"
            write_jsonl(
                older,
                [
                    {
                        "timestamp": "2026-05-28T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "older"},
                    }
                ],
            )
            write_jsonl(
                newer,
                [
                    {
                        "timestamp": "2026-05-28T13:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "newer"},
                    }
                ],
            )
            stale_older_entry = session_cache_entry(
                older,
                older.stat(),
                session_id="older",
                started_at=utc_datetime(2026, 5, 28, 15, 0),
                ended_at=utc_datetime(2026, 5, 28, 15, 0),
            )
            write_session_cache(
                session_cache_path(codex_home),
                {
                    session_cache_key(older): {
                        **stale_older_entry,
                        "size": stale_older_entry["size"] + 1,
                    },
                    session_cache_key(newer): session_cache_entry(
                        newer,
                        newer.stat(),
                        session_id="newer",
                        started_at=utc_datetime(2026, 5, 28, 13, 0),
                        ended_at=utc_datetime(2026, 5, 28, 13, 0),
                    ),
                },
            )

            resolved = resolve_conversion_input(Path("latest"), codex_home)
            cache_entries = read_session_cache(session_cache_path(codex_home))

        self.assertEqual(resolved.path, newer.resolve())
        refreshed_older_entry = cache_entries[session_cache_key(older)]
        self.assertEqual(refreshed_older_entry["ended_at"], "2026-05-28T12:00:00+00:00")

    def test_resolve_conversion_input_latest_refreshes_fingerprint_only_session_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-older.jsonl"
            newer = sessions_day / "rollout-newer.jsonl"
            write_jsonl(
                older,
                [
                    {
                        "timestamp": "2026-05-28T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "older"},
                    }
                ],
            )
            write_jsonl(
                newer,
                [
                    {
                        "timestamp": "2026-05-28T13:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "newer"},
                    }
                ],
            )
            os.utime(older, (1_800_000_100, 1_800_000_100))
            os.utime(newer, (1_800_000_000, 1_800_000_000))
            write_session_cache(
                session_cache_path(codex_home),
                {
                    session_cache_key(older): session_cache_entry(
                        older,
                        older.stat(),
                        fingerprint=FileFingerprint(size=older.stat().st_size, sha256="a" * 64),
                    ),
                    session_cache_key(newer): session_cache_entry(
                        newer,
                        newer.stat(),
                        fingerprint=FileFingerprint(size=newer.stat().st_size, sha256="b" * 64),
                    ),
                },
            )

            resolved = resolve_conversion_input(Path("latest"), codex_home)
            cache_entries = read_session_cache(session_cache_path(codex_home))

        self.assertEqual(resolved.path, newer.resolve())
        self.assertEqual(
            cache_entries[session_cache_key(newer)]["ended_at"], "2026-05-28T13:00:00+00:00"
        )
        self.assertTrue(cache_entries[session_cache_key(newer)]["timestamps_scanned"])
        self.assertEqual(cache_entries[session_cache_key(newer)]["sha256"], "b" * 64)

    def test_resolve_conversion_input_latest_does_not_cache_file_changed_during_scan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            rollout = sessions_day / "rollout-live.jsonl"
            write_jsonl(
                rollout,
                [
                    {
                        "timestamp": "2026-05-28T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "live"},
                    }
                ],
            )

            def mutate_rollout(_path: Path, *, include_ended_at: bool) -> SessionFileMetadata:
                self.assertTrue(include_ended_at)
                with rollout.open("a", encoding="utf-8") as file:
                    file.write(
                        json.dumps(
                            {
                                "timestamp": "2026-05-28T12:01:00Z",
                                "type": "event_msg",
                                "payload": {"type": "agent_message", "message": "still writing"},
                            }
                        )
                        + "\n"
                    )
                timestamp = utc_datetime(2026, 5, 28, 12, 0)
                return SessionFileMetadata(
                    identity=SessionIdentity(session_id="live", is_canonical=False),
                    started_at=timestamp,
                    ended_at=timestamp,
                )

            with patch(
                "codex_sessions.sessions.paths.read_session_file_metadata",
                side_effect=mutate_rollout,
            ):
                resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, rollout.resolve())
        self.assertFalse(session_cache_path(codex_home).exists())

    def test_resolve_conversion_input_accepts_codex_home_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            relative_path = Path("sessions/2026/05/28/rollout.jsonl")
            rollout = codex_home / relative_path
            write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": "session-id"}}])

            resolved = resolve_conversion_input(relative_path, codex_home)

        self.assertEqual(resolved.path, rollout.resolve())
        self.assertIsNone(resolved.output_stem)

    def test_resolve_conversion_input_latest_falls_back_to_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-older.jsonl"
            newer = sessions_day / "rollout-newer.jsonl"
            write_jsonl(older, [{"type": "session_meta", "payload": {"id": "older"}}])
            write_jsonl(newer, [{"type": "session_meta", "payload": {"id": "newer"}}])
            os.utime(older, (1_800_000_000, 1_800_000_000))
            os.utime(newer, (1_800_000_100, 1_800_000_100))

            resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, newer.resolve())

    def test_resolve_conversion_input_accepts_exact_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            session_id = "019f0000-0000-7000-8000-000000000001"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            rollout = sessions_day / f"rollout-2026-05-28T10-00-00-{session_id}.jsonl"
            sessions_day.mkdir(parents=True)
            write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": session_id}}])
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Fix README.md docs"}],
            )

            resolved = resolve_conversion_input(Path("Fix README.md docs"), codex_home)

        self.assertEqual(resolved.path, rollout.resolve())
        self.assertEqual(resolved.output_stem, session_id)


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def utc_datetime(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
