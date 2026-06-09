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
from codex_sessions.sessions.cache import (  # noqa: E402
    session_cache_entry,
    session_cache_key,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.rollout import FileFingerprint  # noqa: E402


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def import_title_record(session_id: str, thread_name: str, timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "thread_name_updated",
            "thread_id": session_id,
            "thread_name": thread_name,
        },
    }


def import_user_message(content: str, timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": content,
        },
    }


class CliImportConflictTests(unittest.TestCase):
    def test_import_skips_identical_existing_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "20202020-2020-2020-2020-202020202020"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            records = [
                {
                    "timestamp": "2026-04-30T18:20:39Z",
                    "type": "session_meta",
                    "payload": {"id": session_id},
                }
            ]
            write_jsonl(source_path, records)
            write_jsonl(target_path, records)

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Sessions added: 0", output)
            self.assertIn("Skipped (identical): 1", output)
            self.assertIn("SKIPPED (identical)", output)
            self.assertIn("sha256", output)

    def test_import_reports_different_existing_rollout_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "21212121-2121-2121-2121-212121212121"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            write_jsonl(
                source_path,
                [
                    import_title_record(
                        session_id, "Incoming conflict title", "2026-04-30T18:20:38Z"
                    ),
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                ],
            )
            write_jsonl(
                target_path,
                [
                    import_title_record(session_id, "Local conflict title", "2026-04-30T18:20:38Z"),
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": "different"},
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("ID conflicts: 1", output)
            self.assertIn("ID conflict", output)
            self.assertIn("Local:", output)
            self.assertIn("Import:", output)
            self.assertIn("Local conflict title", output)
            self.assertIn("Incoming conflict title", output)
            self.assertIn("File:", output)
            self.assertIn("Fingerprint:", output)
            self.assertIn("sha256", output)

    def test_import_conflict_infers_distinct_titles_without_title_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "21212121-2424-2424-2424-242424242424"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            write_jsonl(
                source_path,
                [
                    import_user_message("---\nIncoming first message", "2026-04-30T18:20:38Z"),
                    import_user_message("incoming tail", "2026-04-30T18:20:39Z"),
                ],
            )
            write_jsonl(
                target_path,
                [
                    import_user_message("---\nLocal first message", "2026-04-30T18:20:38Z"),
                    import_user_message("local tail", "2026-04-30T18:20:39Z"),
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("Local first message", output)
            self.assertIn("Incoming first message", output)

    def test_import_conflict_reuses_cached_existing_rollout_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "21212121-3131-3131-3131-313131313131"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            source_path = source_dir / filename
            target_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            target_path.parent.mkdir(parents=True)
            write_jsonl(
                source_path,
                [
                    import_title_record(
                        session_id, "Incoming cached conflict", "2026-04-30T18:20:38Z"
                    ),
                    import_user_message("incoming", "2026-04-30T18:20:39Z"),
                ],
            )
            write_jsonl(
                target_path,
                [
                    import_title_record(
                        session_id, "Local cached conflict", "2026-04-30T18:20:38Z"
                    ),
                    import_user_message("local", "2026-04-30T18:20:39Z"),
                ],
            )
            target_stat = target_path.stat()
            cached_sha = "c" * 64
            write_session_cache(
                session_cache_path(codex_home),
                {
                    session_cache_key(target_path): session_cache_entry(
                        target_path,
                        target_stat,
                        fingerprint=FileFingerprint(size=target_stat.st_size, sha256=cached_sha),
                    )
                },
            )

            with patch(
                "codex_sessions.sessions.cache.file_fingerprint",
                side_effect=AssertionError("cached fingerprint should be reused"),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    result = main(["import", "--codex-home", str(codex_home), str(source_path)])

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("ID conflicts: 1", output)
            self.assertIn(f"sha256 {cached_sha[:12]}", output)


if __name__ == "__main__":
    unittest.main()
