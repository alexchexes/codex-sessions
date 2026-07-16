import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from codex_sessions.search.core import SearchOptions
from codex_sessions.search.sessions import (
    build_search_document,
    render_search_line_groups,
    render_search_text,
    render_tool_call_search_lines,
    render_tool_output_search_lines,
    search_document_lines,
    search_sessions,
)
from codex_sessions.sessions.cache import read_session_cache, session_cache_key, session_cache_path
from codex_sessions.sessions.documents import SearchDocument


def search_options(**overrides: Any) -> SearchOptions:
    values: dict[str, Any] = {
        "pattern": "needle",
        "regex": False,
        "ignore_case": True,
        "line_width": 120,
        "max_lines_per_session": 5,
        "include_metadata": False,
        "include_tools": False,
        "color": "never",
        "redaction": "...",
    }
    values.update(overrides)
    return SearchOptions(**values)


class SessionSearchTests(unittest.TestCase):
    def test_render_search_text_flattens_embedded_json(self) -> None:
        self.assertEqual(
            render_search_text('{"command":"echo hi","items":["a","b"],"empty":null}'),
            "command: echo hi\nitems:\na\nb",
        )

    def test_render_search_line_groups_filters_visible_metadata_and_tools(self) -> None:
        self.assertEqual(
            render_search_line_groups(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                }
            ),
            [("visible", ["User: hello"])],
        )
        self.assertEqual(
            render_search_line_groups(
                {
                    "type": "session_meta",
                    "payload": {"cwd": "D:/repo", "git": {"branch": "main"}},
                }
            ),
            [
                (
                    "metadata",
                    ["Session metadata: cwd: D:/repo", "Session metadata: branch: main"],
                )
            ],
        )

    def test_search_document_lines_deduplicates_and_honors_options(self) -> None:
        document = SearchDocument(
            session_id="id",
            thread_name=None,
            started_at=None,
            ended_at=None,
            last_activity_at=None,
            visible_lines=("User: needle", "User: needle"),
            metadata_lines=("Session metadata: cwd: needle",),
            tool_input_lines=("Tool call: shell_command: input needle",),
            tool_output_lines=("Tool output: shell_command: output needle",),
        )

        self.assertEqual(search_document_lines(document, search_options()), ["User: needle"])
        self.assertEqual(
            search_document_lines(
                document,
                search_options(include_metadata=True, include_tools=True),
            ),
            [
                "User: needle",
                "Session metadata: cwd: needle",
                "Tool call: shell_command: input needle",
                "Tool output: shell_command: output needle",
            ],
        )
        self.assertEqual(
            search_document_lines(
                document,
                search_options(
                    include_tools=True,
                    tool_include=frozenset({"mcp__ask_human.ask_human"}),
                ),
            ),
            ["User: needle"],
        )
        self.assertEqual(
            search_document_lines(
                document,
                search_options(
                    include_visible=False,
                    include_tool_outputs=True,
                    include_titles=False,
                ),
            ),
            [
                "Tool output: shell_command: output needle",
            ],
        )

    def test_search_document_lines_filters_tools_without_filtering_dialogue(self) -> None:
        document = SearchDocument(
            session_id="id",
            thread_name=None,
            started_at=None,
            ended_at=None,
            last_activity_at=None,
            visible_lines=("User: dialogue needle",),
            metadata_lines=(),
            tool_input_lines=(
                "Tool call: mcp__ask_human.ask_human: Question: `input needle`",
                "Tool call: shell_command: echo needle",
            ),
            tool_output_lines=(
                "Tool output: mcp__ask_human.ask_human: output needle",
                "Tool output: shell_command: output needle",
            ),
        )

        self.assertEqual(
            search_document_lines(
                document,
                search_options(
                    include_tools=True,
                    tool_include=frozenset({"*ask_human*"}),
                ),
            ),
            [
                "User: dialogue needle",
                "Tool call: mcp__ask_human.ask_human: Question: `input needle`",
                "Tool output: mcp__ask_human.ask_human: output needle",
            ],
        )

    def test_render_tool_call_search_lines_extracts_command_preview(self) -> None:
        lines = render_tool_call_search_lines(
            {
                "type": "function_call",
                "name": "shell_command",
                "arguments": json.dumps({"command": "echo needle"}),
            }
        )

        self.assertEqual(lines, ["Tool call: shell_command: echo needle"])

    def test_render_tool_call_search_lines_extracts_ask_human_input(self) -> None:
        lines = render_tool_call_search_lines(
            {
                "type": "function_call",
                "namespace": "mcp__ask_human",
                "name": "ask_human",
                "arguments": json.dumps(
                    {"question": "needle question", "context": "needle context"}
                ),
            }
        )

        self.assertEqual(
            lines,
            [
                "Tool call: mcp__ask_human.ask_human: Question: `needle question`",
                "Tool call: mcp__ask_human.ask_human: Context: `needle context`",
            ],
        )

    def test_render_tool_output_search_lines_uses_call_name_mapping(self) -> None:
        lines = render_tool_output_search_lines(
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": json.dumps({"stdout": "needle found", "stderr": ""}),
            },
            {"call_1": "shell_command"},
        )

        self.assertEqual(lines, ["Tool output: shell_command: stdout: needle found"])

    def test_render_search_line_groups_separates_tool_inputs_and_outputs(self) -> None:
        tool_names_by_call_id: dict[str, str] = {}
        input_lines = render_search_line_groups(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "shell_command",
                    "arguments": json.dumps({"command": "echo needle"}),
                },
            },
            tool_names_by_call_id,
        )
        output_lines = render_search_line_groups(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "needle output",
                },
            },
            tool_names_by_call_id,
        )

        self.assertEqual(input_lines, [("tool_inputs", ["Tool call: shell_command: echo needle"])])
        self.assertEqual(
            output_lines,
            [("tool_outputs", ["Tool output: shell_command: needle output"])],
        )

    def test_build_search_document_and_search_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            sessions_dir = codex_home / "sessions" / "2026" / "04" / "30"
            sessions_dir.mkdir(parents=True)
            session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            rollout = sessions_dir / f"rollout-2026-04-30T18-20-39-{session_id}.jsonl"
            rollout.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {
                            "timestamp": "2026-04-30T18:20:39Z",
                            "type": "session_meta",
                            "payload": {"id": session_id},
                        },
                        {
                            "timestamp": "2026-04-30T18:21:39Z",
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": "needle in message",
                            },
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            document = build_search_document(rollout, "...")
            results, warnings = search_sessions(codex_home, search_options())
            metadata_cache = read_session_cache(session_cache_path(codex_home))

        self.assertEqual(document.session_id, session_id)
        self.assertEqual(warnings, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].session.session_id, session_id)
        self.assertEqual(results[0].lines[0].text, "User: needle in message")
        self.assertEqual(
            metadata_cache[session_cache_key(rollout)]["session_id"],
            session_id,
        )


if __name__ == "__main__":
    unittest.main()
