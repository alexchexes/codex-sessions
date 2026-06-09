import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402
from codex_sessions.sessions.cache import (  # noqa: E402
    read_session_cache,
    session_cache_path,
)


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


class CliImportBulkTests(unittest.TestCase):
    def test_import_directory_imports_safe_sessions_and_reports_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")
            new_id = "46464646-4646-4646-4646-464646464646"
            identical_id = "47474747-4747-4747-4747-474747474747"
            conflict_id = "48484848-4848-4848-4848-484848484848"

            new_source = source_dir / f"rollout-2026-04-30T18-20-39-{new_id}.jsonl"
            identical_source = source_dir / f"rollout-2026-04-30T18-21-39-{identical_id}.jsonl"
            conflict_source = source_dir / f"rollout-2026-04-30T18-22-39-{conflict_id}.jsonl"
            write_jsonl(
                new_source,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": new_id,
                            "thread_name": "Bulk imported title",
                        },
                    }
                ],
            )
            identical_records = [
                {
                    "timestamp": "2026-04-30T18:21:39Z",
                    "type": "session_meta",
                    "payload": {"id": identical_id},
                }
            ]
            write_jsonl(identical_source, identical_records)
            write_jsonl(sessions_day / identical_source.name, identical_records)
            write_jsonl(
                conflict_source,
                [
                    {
                        "timestamp": "2026-04-30T18:22:39Z",
                        "type": "session_meta",
                        "payload": {"id": conflict_id},
                    }
                ],
            )
            write_jsonl(
                sessions_day / conflict_source.name,
                [
                    {
                        "timestamp": "2026-04-30T18:22:39Z",
                        "type": "session_meta",
                        "payload": {"id": conflict_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:23:39Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": "different"},
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_dir)])

            output = buffer.getvalue()
            imported_path = sessions_day / new_source.name
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            state_backups = tuple(
                (codex_home / "backups" / "codex-sessions").glob("*/state_5.sqlite")
            )
            self.assertEqual(result, 1)
            self.assertIn("Sessions added: 1", output)
            self.assertIn("Skipped (identical): 1", output)
            self.assertIn("ID conflicts: 1", output)
            self.assertTrue(imported_path.exists())
            self.assertEqual(index_records[0]["id"], new_id)
            self.assertFalse(state_db.exists())
            self.assertEqual(len(state_backups), 1)

    def test_import_directory_reports_duplicates_without_importing_duplicate_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            safe_id = "51515151-5151-5151-5151-515151515151"
            duplicate_id = "52525252-5252-5252-5252-525252525252"
            safe_source = source_dir / f"rollout-2026-04-30T18-20-39-{safe_id}.jsonl"
            duplicate_first = source_dir / f"rollout-2026-04-30T18-21-39-{duplicate_id}.jsonl"
            duplicate_second = source_dir / f"rollout-2026-04-30T18-22-39-{duplicate_id}.jsonl"
            write_jsonl(
                safe_source,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": safe_id,
                            "thread_name": "Safe duplicate import neighbor",
                        },
                    }
                ],
            )
            write_jsonl(
                duplicate_first,
                [
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "session_meta",
                        "payload": {"id": duplicate_id},
                    }
                ],
            )
            write_jsonl(
                duplicate_second,
                [
                    {
                        "timestamp": "2026-04-30T18:22:39Z",
                        "type": "session_meta",
                        "payload": {"id": duplicate_id},
                    }
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_dir)])

            output = buffer.getvalue()
            imported_files = sorted((codex_home / "sessions").rglob("*.jsonl"))
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 1)
            self.assertIn("Sessions added: 1", output)
            self.assertIn("Duplicates: 1", output)
            self.assertIn(f"DUPLICATE {duplicate_id}", output)
            self.assertIn(str(duplicate_first.resolve()), output)
            self.assertIn(str(duplicate_second.resolve()), output)
            self.assertEqual([record["id"] for record in index_records], [safe_id])
            self.assertEqual(len(imported_files), 1)
            self.assertEqual(imported_files[0].name, safe_source.name)

    def test_import_zip_imports_all_rollouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_zip = root / "sessions.zip"
            first_id = "49494949-4949-4949-4949-494949494949"
            second_id = "50505050-5050-5050-5050-505050505050"
            first_records = [
                {
                    "timestamp": "2026-04-30T18:20:39Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_name_updated",
                        "thread_id": first_id,
                        "thread_name": "First zip import",
                    },
                }
            ]
            second_records = [
                {
                    "timestamp": "2026-05-01T18:20:39Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "thread_name_updated",
                        "thread_id": second_id,
                        "thread_name": "Second zip import",
                    },
                }
            ]
            with zipfile.ZipFile(source_zip, "w") as archive:
                archive.writestr(
                    f"2026-04-30--First-zip-import--{first_id}.jsonl",
                    "\n".join(json.dumps(record) for record in first_records) + "\n",
                )
                archive.writestr(
                    f"2026-05-01--Second-zip-import--{second_id}.jsonl",
                    "\n".join(json.dumps(record) for record in second_records) + "\n",
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_zip)])

            output = buffer.getvalue()
            imported_files = sorted((codex_home / "sessions").rglob("*.jsonl"))
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 0)
            self.assertIn("Sessions added: 2", output)
            self.assertEqual(len(imported_files), 2)
            self.assertEqual(
                [record["thread_name"] for record in index_records],
                ["First zip import", "Second zip import"],
            )

    def test_import_zip_preserves_rollout_basename_for_filename_date_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_zip = root / "sessions.zip"
            session_id = "53535353-5353-5353-5353-535353535353"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            records = [{"type": "session_meta", "payload": {"id": session_id}}]
            with zipfile.ZipFile(source_zip, "w") as archive:
                archive.writestr(
                    f"nested/{filename}",
                    "\n".join(json.dumps(record) for record in records) + "\n",
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_zip)])

            output = buffer.getvalue()
            imported_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            index_records = read_jsonl(codex_home / "session_index.jsonl")
            self.assertEqual(result, 0)
            self.assertIn("Imported session:", output)
            self.assertTrue(imported_path.exists())
            self.assertEqual(index_records[0]["id"], session_id)

    def test_import_zip_caches_local_existing_fingerprints_not_temp_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_zip = root / "sessions.zip"
            session_id = "53535353-6464-6464-6464-646464646464"
            filename = f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            local_path = codex_home / "sessions" / "2026" / "04" / "30" / filename
            local_path.parent.mkdir(parents=True)
            write_jsonl(
                local_path,
                [
                    import_title_record(
                        session_id, "Local zip cache conflict", "2026-04-30T18:20:38Z"
                    ),
                    import_user_message("local", "2026-04-30T18:20:39Z"),
                ],
            )
            incoming_records = [
                import_title_record(
                    session_id, "Incoming zip cache conflict", "2026-04-30T18:20:38Z"
                ),
                import_user_message("incoming", "2026-04-30T18:20:39Z"),
            ]
            with zipfile.ZipFile(source_zip, "w") as archive:
                archive.writestr(
                    f"nested/{filename}",
                    "\n".join(json.dumps(record) for record in incoming_records) + "\n",
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_zip)])

            cache_entries = read_session_cache(session_cache_path(codex_home))
            cached_paths = {
                path
                for entry in cache_entries.values()
                if isinstance(entry, dict) and isinstance((path := entry.get("path")), str)
            }
            self.assertEqual(result, 1)
            self.assertIn(str(local_path.resolve()), cached_paths)
            self.assertEqual(cached_paths, {str(local_path.resolve())})
            self.assertTrue(all("nested" not in path for path in cached_paths))

    def test_import_directory_uses_manifest_fingerprint_for_incoming_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "53535353-6565-6565-6565-656565656565"
            filename = f"2026-04-30--Manifest-import--{session_id}.jsonl"
            source_path = source_dir / filename
            records = [
                import_title_record(session_id, "Manifest import", "2026-04-30T18:20:38Z"),
                import_user_message("incoming", "2026-04-30T18:20:39Z"),
            ]
            write_jsonl(source_path, records)
            source_bytes = source_path.read_bytes()
            (source_dir / "codex-sessions-manifest-v1.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "rollouts": [
                            {
                                "path": filename,
                                "session_id": session_id,
                                "thread_name": "Manifest import",
                                "started_at": "2026-04-30T18:20:38+00:00",
                                "updated_at": "2026-04-30T18:20:39+00:00",
                                "size": len(source_bytes),
                                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "codex_sessions.sessions.transfer.file_fingerprint",
                side_effect=AssertionError("source manifest fingerprint should be reused"),
            ):
                with redirect_stdout(StringIO()):
                    result = main(["import", "--codex-home", str(codex_home), str(source_dir)])

            self.assertEqual(result, 0)
            self.assertEqual(read_jsonl(codex_home / "session_index.jsonl")[0]["id"], session_id)

    def test_import_malformed_manifest_warns_and_falls_back_to_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_dir = root / "incoming"
            source_dir.mkdir()
            session_id = "53535353-6666-6666-6666-666666666666"
            source_path = source_dir / f"2026-04-30--Bad-manifest--{session_id}.jsonl"
            write_jsonl(
                source_path,
                [import_title_record(session_id, "Bad manifest import", "2026-04-30T18:20:38Z")],
            )
            (source_dir / "codex-sessions-manifest-v1.json").write_text(
                "{not valid json",
                encoding="utf-8",
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_dir)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Warnings:", output)
            self.assertIn("Could not use export manifest", output)
            self.assertIn("Falling back to hashing", output)
            self.assertEqual(read_jsonl(codex_home / "session_index.jsonl")[0]["id"], session_id)

    def test_import_zip_malformed_manifest_warns_and_falls_back_to_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            source_zip = root / "incoming.zip"
            session_id = "53535353-6767-6767-6767-676767676767"
            records = [
                import_title_record(session_id, "Zip bad manifest import", "2026-04-30T18:20:38Z")
            ]
            with zipfile.ZipFile(source_zip, "w") as archive:
                archive.writestr("codex-sessions-manifest-v1.json", "{not valid json")
                archive.writestr(
                    f"2026-04-30--Zip-bad-manifest--{session_id}.jsonl",
                    "\n".join(json.dumps(record) for record in records) + "\n",
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["import", "--codex-home", str(codex_home), str(source_zip)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Warnings:", output)
            self.assertIn("Could not use export manifest", output)
            self.assertEqual(read_jsonl(codex_home / "session_index.jsonl")[0]["id"], session_id)


if __name__ == "__main__":
    unittest.main()
