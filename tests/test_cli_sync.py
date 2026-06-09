import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402


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


class CliSyncTests(unittest.TestCase):
    def test_sync_dry_run_reports_download_and_upload_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sync_dir = root / "sync"
            local_day = codex_home / "sessions" / "2026" / "04" / "30"
            sync_dir.mkdir()
            local_day.mkdir(parents=True)
            local_id = "68686868-6868-6868-6868-686868686868"
            remote_id = "69696969-6969-6969-6969-696969696969"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": local_id, "thread_name": "Local sync title"}],
            )
            write_jsonl(
                local_day / f"rollout-2026-04-30T18-20-39-{local_id}.jsonl",
                [import_title_record(local_id, "Local sync title", "2026-04-30T18:20:39Z")],
            )
            remote_source = sync_dir / f"2026-05-01--Remote-sync-title--{remote_id}.jsonl"
            write_jsonl(
                remote_source,
                [import_title_record(remote_id, "Remote sync title", "2026-05-01T18:20:39Z")],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["sync", "--dry-run", "--codex-home", str(codex_home), str(sync_dir)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Download from sync folder:", output)
            self.assertIn(remote_id, output)
            self.assertIn("Upload to sync folder:", output)
            self.assertIn("Would export local-only sessions: 1", output)
            self.assertFalse(
                (codex_home / "sessions" / "2026" / "05" / "01" / remote_source.name).exists()
            )
            self.assertEqual(len(list(sync_dir.glob(f"*{local_id}.jsonl"))), 0)

    def test_sync_imports_remote_and_exports_local_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sync_dir = root / "sync"
            local_day = codex_home / "sessions" / "2026" / "04" / "30"
            sync_dir.mkdir()
            local_day.mkdir(parents=True)
            local_id = "70707070-7070-7070-7070-707070707070"
            remote_id = "71717171-7171-7171-7171-717171717171"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": local_id, "thread_name": "Local sync apply"}],
            )
            write_jsonl(
                local_day / f"rollout-2026-04-30T18-20-39-{local_id}.jsonl",
                [import_title_record(local_id, "Local sync apply", "2026-04-30T18:20:39Z")],
            )
            remote_source = sync_dir / f"2026-05-01--Remote-sync-apply--{remote_id}.jsonl"
            write_jsonl(
                remote_source,
                [import_title_record(remote_id, "Remote sync apply", "2026-05-01T18:20:39Z")],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["sync", "--codex-home", str(codex_home), str(sync_dir)])

            output = buffer.getvalue()
            local_uploads = list(sync_dir.glob(f"*{local_id}.jsonl"))
            imported_remote = list((codex_home / "sessions").rglob(f"*{remote_id}.jsonl"))
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 0)
            self.assertIn("Download from sync folder:", output)
            self.assertIn("Upload to sync folder:", output)
            self.assertEqual(len(local_uploads), 1)
            self.assertEqual(len(imported_remote), 1)
            self.assertEqual(
                [record["id"] for record in index_records],
                [local_id, remote_id],
            )
            self.assertTrue((sync_dir / "codex-sessions-manifest-v1.json").exists())

    def test_sync_does_not_overwrite_same_id_session_in_sync_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sync_dir = root / "sync"
            local_day = codex_home / "sessions" / "2026" / "04" / "30"
            sync_dir.mkdir()
            local_day.mkdir(parents=True)
            session_id = "72727272-7272-7272-7272-727272727272"
            local_records = [
                import_title_record(session_id, "Local ahead sync", "2026-04-30T18:20:39Z"),
                import_user_message("local tail", "2026-04-30T18:21:39Z"),
            ]
            remote_records = [
                import_title_record(session_id, "Remote older sync", "2026-04-30T18:20:39Z")
            ]
            local_path = local_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            remote_path = sync_dir / f"2026-04-30--Remote-older-sync--{session_id}.jsonl"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Local ahead sync"}],
            )
            write_jsonl(local_path, local_records)
            write_jsonl(remote_path, remote_records)
            original_remote_text = remote_path.read_text(encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["sync", "--codex-home", str(codex_home), str(sync_dir)])

            output = buffer.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("ID conflicts: 1", output)
            self.assertIn("Exported local-only sessions: 0", output)
            self.assertEqual(remote_path.read_text(encoding="utf-8"), original_remote_text)


if __name__ == "__main__":
    unittest.main()
