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
from codex_sessions.core.timestamps import parse_timestamp  # noqa: E402
from codex_sessions.search.cache import search_cache_path  # noqa: E402
from codex_sessions.sessions.display import (  # noqa: E402
    format_local_timestamp,
    local_timezone_offset_label,
)
from codex_sessions.sessions.index_workflows import list_session_lines  # noqa: E402


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


class CliListTests(unittest.TestCase):
    def test_list_sessions_cross_checks_index_and_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            matched_id = "019c8599-6845-7772-9c64-5f0ee47c73f1"
            missing_file_id = "11111111-1111-1111-1111-111111111111"
            orphan_id = "22222222-2222-2222-2222-222222222222"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {
                        "id": matched_id,
                        "thread_name": "Add scope for type casting types",
                        "updated_at": "2026-03-06T13:24:38.0294272Z",
                    },
                    {"id": missing_file_id, "thread_name": "Missing rollout"},
                ],
            )
            matched_path = sessions_day / f"rollout-2026-04-30T18-20-39-{matched_id}.jsonl"
            write_jsonl(
                matched_path,
                [
                    {
                        "timestamp": "2026-02-22T13:48:23.714Z",
                        "type": "session_meta",
                        "payload": {"id": matched_id},
                    },
                    {
                        "timestamp": "2026-02-22T13:50:54.380Z",
                        "type": "event_msg",
                        "payload": {"type": "turn_aborted"},
                    },
                ],
            )
            orphan_path = sessions_day / f"rollout-2026-04-30T18-21-40-{orphan_id}.jsonl"
            orphan_path.write_text("", encoding="utf-8")

            lines = list_session_lines(codex_home)
            started_at = parse_timestamp("2026-02-22T13:48:23.714Z")
            ended_at = parse_timestamp("2026-02-22T13:50:54.380Z")

            self.assertEqual(
                lines,
                [
                    (
                        f"{format_local_timestamp(started_at)} - "
                        f"{format_local_timestamp(ended_at)} "
                        f"({local_timezone_offset_label(ended_at)}) - "
                        f"{matched_id} - "
                        "Add scope for type casting types"
                    ),
                    f"{missing_file_id} - Missing rollout - NO ROLLOUT FILE",
                    f"2026/04/30/{orphan_path.name} - NO ENTRY IN session_index.jsonl",
                ],
            )

    def test_list_sessions_infers_title_for_unindexed_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "12121212-1212-1212-1212-121212121212"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Please add export support. Some extra detail follows.",
                        },
                    }
                ],
            )

            lines = list_session_lines(codex_home)
            started_at = parse_timestamp("2026-04-30T18:20:39Z")

            self.assertEqual(
                lines,
                [
                    (
                        f"{format_local_timestamp(started_at)} - "
                        f"{format_local_timestamp(started_at)} "
                        f"({local_timezone_offset_label(started_at)}) - "
                        f"{session_id} - Please add export support. - "
                        "NO ENTRY IN session_index.jsonl"
                    )
                ],
            )

    def test_list_sessions_skips_injected_context_when_inferring_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "45454545-4545-4545-4545-454545454545"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "<environment_context>\n<cwd>D:\\repos</cwd>",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Please sync this session to the Mac.",
                        },
                    },
                ],
            )

            lines = list_session_lines(codex_home)

            self.assertEqual(len(lines), 1)
            self.assertIn("Please sync this session to the Mac.", lines[0])
            self.assertNotIn("environment_context", lines[0])

    def test_list_sessions_infers_title_from_request_inside_ide_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "89898989-8989-8989-8989-898989898989"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": (
                                "# Context from my IDE setup:\n\n"
                                "## Active file: cli.py\n\n"
                                "## My request for Codex:\n"
                                "Please repair the index title."
                            ),
                        },
                    }
                ],
            )

            lines = list_session_lines(codex_home)

            self.assertEqual(len(lines), 1)
            self.assertIn("Please repair the index title.", lines[0])
            self.assertNotIn("Context from my IDE setup", lines[0])

    def test_list_sessions_infers_title_from_event_message_ide_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "bcbcbcbc-bcbc-bcbc-bcbc-bcbcbcbcbcbc"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": (
                                "# Context from my IDE setup:\n\n"
                                "## Active file: cli.py\n\n"
                                "## My request for Codex:\n"
                                "Please title this event message."
                            ),
                        },
                    }
                ],
            )

            lines = list_session_lines(codex_home)

            self.assertEqual(len(lines), 1)
            self.assertIn("Please title this event message.", lines[0])
            self.assertNotIn("Context from my IDE setup", lines[0])

    def test_list_sessions_prefers_thread_name_updated_title_from_rollout(self) -> None:
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
                        "type": "event_msg",
                        "payload": {
                            "type": "thread_name_updated",
                            "thread_id": session_id,
                            "thread_name": "Rollout title wins",
                        },
                    },
                    {
                        "timestamp": "2026-04-30T18:21:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "This fallback title should not be used.",
                        },
                    },
                ],
            )

            lines = list_session_lines(codex_home)

            self.assertEqual(len(lines), 1)
            self.assertIn("Rollout title wins", lines[0])
            self.assertNotIn("This fallback title", lines[0])

    def test_list_sessions_reuses_cached_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "23232323-2323-2323-2323-232323232323"
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "Cached list title",
                        },
                    }
                ],
            )

            first_lines = list_session_lines(codex_home)
            self.assertTrue(search_cache_path(codex_home).exists())

            with patch(
                "codex_sessions.sessions.documents.iter_jsonl_objects",
                side_effect=AssertionError("list should reuse cached session metadata"),
            ):
                second_lines = list_session_lines(codex_home)

            self.assertEqual(second_lines, first_lines)
            self.assertIn("Cached list title", second_lines[0])

    def test_list_sessions_reads_session_id_from_metadata_when_filename_has_no_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "33333333-3333-3333-3333-333333333333"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {
                        "id": session_id,
                        "thread_name": "Metadata id",
                        "updated_at": "2026-04-30T19:01:00Z",
                    }
                ],
            )
            session_path = sessions_day / "rollout.jsonl"
            write_jsonl(
                session_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:00Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T19:15:30Z",
                        "type": "event_msg",
                        "payload": {"type": "task_complete"},
                    },
                ],
            )

            lines = list_session_lines(codex_home)
            started_at = parse_timestamp("2026-04-30T18:20:00Z")
            ended_at = parse_timestamp("2026-04-30T19:15:30Z")

            self.assertEqual(
                lines,
                [
                    (
                        f"{format_local_timestamp(started_at)} - "
                        f"{format_local_timestamp(ended_at)} "
                        f"({local_timezone_offset_label(ended_at)}) - "
                        f"{session_id} - Metadata id"
                    )
                ],
            )

    def test_list_sessions_accepts_concatenated_index_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            codex_home.joinpath("sessions").mkdir()

            first_id = "55555555-5555-5555-5555-555555555555"
            second_id = "66666666-6666-6666-6666-666666666666"
            records = [
                {"id": first_id, "thread_name": "First"},
                {"id": second_id, "thread_name": "Second"},
            ]
            codex_home.joinpath("session_index.jsonl").write_text(
                "".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            lines = list_session_lines(codex_home)

            self.assertEqual(
                lines,
                [
                    f"{first_id} - First - NO ROLLOUT FILE",
                    f"{second_id} - Second - NO ROLLOUT FILE",
                ],
            )

    def test_list_command_prints_session_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            session_id = "44444444-4444-4444-4444-444444444444"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "CLI list"}],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["list", "--codex-home", str(codex_home)])

            self.assertEqual(result, 0)
            self.assertEqual(
                buffer.getvalue().splitlines(),
                [f"{session_id} - CLI list - NO ROLLOUT FILE"],
            )


if __name__ == "__main__":
    unittest.main()
