import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.cli import main  # noqa: E402
from codex_sessions.sessions.files import session_id_from_path  # noqa: E402


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    session_id = session_id_from_path(path)
    if session_id and (not records or records[0].get("type") != "session_meta"):
        records = [{"type": "session_meta", "payload": {"id": session_id}}, *records]
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


class CliExportBulkTests(unittest.TestCase):
    def test_export_all_to_directory_writes_each_selected_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            output_dir = root / "exports"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            first_id = "39393939-3939-3939-3939-393939393939"
            second_id = "40404040-4040-4040-4040-404040404040"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": first_id, "thread_name": "First bulk export"},
                    {"id": second_id, "thread_name": "Second bulk export"},
                ],
            )
            for session_id, title, minute in (
                (first_id, "Old first title", 20),
                (second_id, "Old second title", 21),
            ):
                write_jsonl(
                    sessions_day / f"rollout-2026-04-30T18-{minute:02d}-39-{session_id}.jsonl",
                    [
                        {
                            "timestamp": f"2026-04-30T18:{minute:02d}:39Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "thread_name_updated",
                                "thread_id": session_id,
                                "thread_name": title,
                            },
                        }
                    ],
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--all",
                        "-o",
                        str(output_dir),
                    ]
                )

            output = buffer.getvalue()
            first_output = output_dir / f"2026-04-30--First-bulk-export--{first_id}.jsonl"
            second_output = output_dir / f"2026-04-30--Second-bulk-export--{second_id}.jsonl"
            manifest_path = output_dir / "codex-sessions-manifest-v1.json"
            self.assertEqual(result, 0)
            self.assertIn("Exported sessions: 2", output)
            self.assertNotIn(first_id, output)
            self.assertNotIn(second_id, output)
            self.assertTrue(first_output.exists())
            self.assertTrue(second_output.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_rollouts = manifest["rollouts"]
            self.assertEqual(manifest["version"], 1)
            self.assertEqual(
                [entry["path"] for entry in manifest_rollouts],
                [first_output.name, second_output.name],
            )
            self.assertEqual(
                [entry["session_id"] for entry in manifest_rollouts],
                [first_id, second_id],
            )
            self.assertEqual(
                [entry["thread_name"] for entry in manifest_rollouts],
                ["First bulk export", "Second bulk export"],
            )
            self.assertEqual(manifest_rollouts[0]["size"], first_output.stat().st_size)
            self.assertEqual(len(manifest_rollouts[0]["sha256"]), 64)
            self.assertEqual(
                read_jsonl(first_output)[1]["payload"]["thread_name"], "First bulk export"
            )
            self.assertEqual(
                read_jsonl(second_output)[1]["payload"]["thread_name"], "Second bulk export"
            )

    def test_export_filters_by_updated_time_and_except_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            output_dir = root / "exports"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "02"
            sessions_day.mkdir(parents=True)
            first_id = "41414141-4141-4141-4141-414141414141"
            second_id = "42424242-4242-4242-4242-424242424242"
            third_id = "43434343-4343-4343-4343-434343434343"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": first_id, "thread_name": "Old filtered export"},
                    {"id": second_id, "thread_name": "Included filtered export"},
                    {"id": third_id, "thread_name": "Excluded filtered export"},
                ],
            )
            for session_id, title, day in (
                (first_id, "Old filtered export", "2026-04-30"),
                (second_id, "Included filtered export", "2026-05-02"),
                (third_id, "Excluded filtered export", "2026-05-03"),
            ):
                write_jsonl(
                    sessions_day / f"rollout-{day}T18-20-39-{session_id}.jsonl",
                    [
                        {
                            "timestamp": f"{day}T18:20:39Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "thread_name_updated",
                                "thread_id": session_id,
                                "thread_name": title,
                            },
                        }
                    ],
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--updated-after",
                        "2026-05-01",
                        "--except",
                        third_id,
                        "-o",
                        str(output_dir),
                    ]
                )

            output = buffer.getvalue()
            exported_files = list(output_dir.glob("*.jsonl"))
            self.assertEqual(result, 0)
            self.assertEqual(len(exported_files), 1)
            self.assertEqual(
                exported_files[0].name,
                f"2026-05-02--Included-filtered-export--{second_id}.jsonl",
            )
            self.assertIn("Sessions filtered out: 2", output)

    def test_export_all_requires_output_directory_or_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "44444444-4444-4444-4444-444444444444"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    }
                ],
            )

            with self.assertRaises(SystemExit) as raised:
                main(["export", "--codex-home", str(codex_home), "--all"])

            self.assertIn("Bulk export requires --output/-o", str(raised.exception))

    def test_export_all_to_zip_replaces_existing_only_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            output_zip = root / "sessions.zip"
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)
            session_id = "45454545-4545-4545-4545-454545454545"
            output_zip.write_text("existing", encoding="utf-8")
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Zip export title"}],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Zip export title",
                        },
                    }
                ],
            )

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "export",
                        "--codex-home",
                        str(codex_home),
                        "--all",
                        "-o",
                        str(output_zip),
                    ]
                )

            self.assertIn("Output zip already exists", str(raised.exception))

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "export",
                        "--force",
                        "--codex-home",
                        str(codex_home),
                        "--all",
                        "-o",
                        str(output_zip),
                    ]
                )

            self.assertEqual(result, 0)
            with zipfile.ZipFile(output_zip) as archive:
                rollout_member = f"2026-04-30--Zip-export-title--{session_id}.jsonl"
                self.assertEqual(
                    archive.namelist(), [rollout_member, "codex-sessions-manifest-v1.json"]
                )
                exported_records = [
                    json.loads(line)
                    for line in archive.read(rollout_member).decode("utf-8").splitlines()
                ]
                manifest = json.loads(
                    archive.read("codex-sessions-manifest-v1.json").decode("utf-8")
                )
            self.assertEqual(exported_records[1]["payload"]["thread_name"], "Zip export title")
            self.assertEqual(manifest["rollouts"][0]["path"], rollout_member)
            self.assertEqual(manifest["rollouts"][0]["session_id"], session_id)


if __name__ == "__main__":
    unittest.main()
