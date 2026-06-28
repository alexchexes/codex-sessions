import json
import os
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
from codex_sessions.sessions.files import session_id_from_path  # noqa: E402


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    session_id = session_id_from_path(path)
    if session_id and (not records or records[0].get("type") != "session_meta"):
        records = [{"type": "session_meta", "payload": {"id": session_id}}, *records]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


class CliFindScopeTests(unittest.TestCase):
    def test_find_scopes_search_to_session_id_or_exact_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            first_id = "12121212-1212-1212-1212-121212121212"
            second_id = "23232323-2323-2323-2323-232323232323"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": first_id, "thread_name": "First scoped session"},
                    {"id": second_id, "thread_name": "Second scoped session"},
                ],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{first_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": first_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from first session",
                        },
                    },
                ],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{second_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": second_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from second session",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                id_result = main(
                    [
                        "find",
                        "--session",
                        first_id,
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )
            id_output = buffer.getvalue()

            buffer = StringIO()
            with redirect_stdout(buffer):
                title_result = main(
                    [
                        "find",
                        "--session",
                        "Second scoped session",
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )
            title_output = buffer.getvalue()

            self.assertEqual(id_result, 0)
            self.assertIn("needle from first session", id_output)
            self.assertNotIn("needle from second session", id_output)
            self.assertEqual(title_result, 0)
            self.assertIn("needle from second session", title_output)
            self.assertNotIn("needle from first session", title_output)

    def test_find_scopes_search_to_multiple_session_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            first_id = "34343434-3434-3434-3434-343434343434"
            second_id = "45454545-4545-4545-4545-454545454545"
            third_id = "56565656-5656-5656-5656-565656565656"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [
                    {"id": first_id, "thread_name": "First target"},
                    {"id": second_id, "thread_name": "Second target"},
                    {"id": third_id, "thread_name": "Third target"},
                ],
            )
            for session_id, marker in [
                (first_id, "first"),
                (second_id, "second"),
                (third_id, "third"),
            ]:
                write_jsonl(
                    sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                    [
                        {
                            "timestamp": "2026-04-30T18:20:39Z",
                            "type": "session_meta",
                            "payload": {"id": session_id},
                        },
                        {
                            "timestamp": "2026-04-30T18:21:00Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": f"needle from {marker}",
                            },
                        },
                    ],
                )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--session",
                        first_id,
                        "--session",
                        "Second target",
                        "--session",
                        first_id,
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("needle from first", output)
            self.assertIn("needle from second", output)
            self.assertNotIn("needle from third", output)
            self.assertEqual(output.count("needle from first"), 1)

    def test_find_scopes_search_to_rollout_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            selected_id = "67676767-6767-6767-6767-676767676767"
            other_id = "78787878-7878-7878-7878-787878787878"
            selected_path = sessions_day / f"rollout-2026-04-30T18-20-39-{selected_id}.jsonl"
            write_jsonl(
                selected_path,
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": selected_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from selected path",
                        },
                    },
                ],
            )
            write_jsonl(
                sessions_day / f"rollout-2026-04-30T18-20-39-{other_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": other_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from other path",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--session",
                        str(selected_path),
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("needle from selected path", output)
            self.assertNotIn("needle from other path", output)

    def test_find_scopes_search_to_latest_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_day = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_day.mkdir(parents=True)

            older_id = "89898989-8989-8989-8989-898989898989"
            newer_id = "90909090-9090-9090-9090-909090909090"
            older_path = sessions_day / f"rollout-2026-04-30T18-20-39-{older_id}.jsonl"
            newer_path = sessions_day / f"rollout-2026-04-30T18-20-39-{newer_id}.jsonl"
            write_jsonl(
                older_path,
                [
                    {
                        "timestamp": "2026-04-30T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": older_id},
                    },
                    {
                        "timestamp": "2026-04-30T12:01:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from older session",
                        },
                    },
                ],
            )
            write_jsonl(
                newer_path,
                [
                    {
                        "timestamp": "2026-04-30T13:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": newer_id},
                    },
                    {
                        "timestamp": "2026-04-30T13:01:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from latest session",
                        },
                    },
                ],
            )
            os.utime(older_path, (1_800_000_100, 1_800_000_100))
            os.utime(newer_path, (1_800_000_000, 1_800_000_000))

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--session",
                        "latest",
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("needle from latest session", output)
            self.assertNotIn("needle from older session", output)

    def test_find_session_scope_uses_custom_sessions_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / ".codex"
            custom_sessions_day = root / "custom-sessions" / "2026" / "04" / "30"
            custom_sessions_day.mkdir(parents=True)
            codex_home.mkdir()

            session_id = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Custom sessions dir"}],
            )
            write_jsonl(
                custom_sessions_day / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl",
                [
                    {
                        "timestamp": "2026-04-30T18:20:39Z",
                        "type": "session_meta",
                        "payload": {"id": session_id},
                    },
                    {
                        "timestamp": "2026-04-30T18:21:00Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": "needle from custom sessions dir",
                        },
                    },
                ],
            )

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(
                    [
                        "find",
                        "--session",
                        session_id,
                        "needle",
                        "--codex-home",
                        str(codex_home),
                        "--sessions-dir",
                        str(root / "custom-sessions"),
                    ]
                )

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("needle from custom sessions dir", output)

    def test_find_session_scope_reports_unknown_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            codex_home.joinpath("sessions").mkdir()

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "find",
                        "--session",
                        "Missing session",
                        "needle",
                        "--codex-home",
                        str(codex_home),
                    ]
                )

        self.assertEqual(
            str(raised.exception),
            "session_index.jsonl not found: " + str((codex_home / "session_index.jsonl").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
