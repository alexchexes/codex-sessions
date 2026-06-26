import json
import os
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

from codex_sessions.cli import (  # noqa: E402
    main,
)
from codex_sessions.cli_args import (  # noqa: E402
    cli_prog_from_argv0,
    parse_search_targets,
)
from codex_sessions.core.terminal import (  # noqa: E402
    console_color_options,
    encode_for_output,
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


class CliTests(unittest.TestCase):
    def test_short_cli_entry_point_is_configured(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('codex-sessions = "codex_sessions.cli:main"', pyproject)
        self.assertEqual(pyproject.count('codex-sessions = "codex_sessions.cli:main"'), 1)

    def test_cli_prog_prefers_short_name(self) -> None:
        self.assertEqual(cli_prog_from_argv0("codex-sessions.exe"), "codex-sessions")
        self.assertEqual(cli_prog_from_argv0("anything.exe"), "codex-sessions")
        self.assertEqual(cli_prog_from_argv0("script.py"), "codex-sessions")

    def test_parse_search_targets_expands_aliases(self) -> None:
        self.assertEqual(
            parse_search_targets(["visible,tools", "meta"]),
            {"visible", "metadata", "tool-inputs", "tool-outputs"},
        )
        with self.assertRaisesRegex(ValueError, "Unknown --search-in target"):
            parse_search_targets(["not-a-target"])

    def test_find_rejects_search_in_with_broad_target_flags(self) -> None:
        with self.assertRaisesRegex(
            SystemExit,
            "--search-in cannot be combined with --metadata, --tools, or --all",
        ):
            main(["find", "--search-in", "tool-outputs", "--tools", "needle"])

    def test_install_skill_command_installs_codex_sessions_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / ".codex"
            skills_dir = root / ".agents" / "skills"
            legacy_skill = codex_home / "skills" / "read-codex-session"
            legacy_skill.mkdir(parents=True)
            legacy_skill.joinpath("SKILL.md").write_text("old skill", encoding="utf-8")

            buffer = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), redirect_stdout(buffer):
                result = main(["install-skill", "--skills-dir", str(skills_dir)])

            installed_skill = skills_dir / "codex-sessions"
            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertTrue(installed_skill.joinpath("SKILL.md").is_file())
            self.assertFalse(legacy_skill.exists())
            self.assertIn("Installed Codex skill", output)
            self.assertIn("Removed old skill", output)

    def test_color_auto_forces_terminal_for_git_bash_pipe(self) -> None:
        git_bash_env = {"TERM": "xterm-256color", "MSYSTEM": "MINGW64"}
        with patch(
            "codex_sessions.core.terminal.is_windows_pipe_stream",
            return_value=True,
        ):
            self.assertEqual(
                console_color_options("auto", StringIO(), git_bash_env),
                (True, False),
            )

    def test_color_auto_does_not_force_for_git_bash_disk_redirect(self) -> None:
        git_bash_env = {"TERM": "xterm-256color", "MSYSTEM": "MINGW64"}
        with patch(
            "codex_sessions.core.terminal.is_windows_pipe_stream",
            return_value=False,
        ):
            self.assertEqual(
                console_color_options("auto", StringIO(), git_bash_env),
                (None, False),
            )

    def test_color_auto_honors_standard_color_environment_flags(self) -> None:
        self.assertEqual(
            console_color_options("auto", StringIO(), {"NO_COLOR": "1"}),
            (None, True),
        )
        self.assertEqual(
            console_color_options("auto", StringIO(), {"CLICOLOR": "0"}),
            (None, True),
        )
        self.assertEqual(
            console_color_options("auto", StringIO(), {"FORCE_COLOR": "1"}),
            (True, False),
        )

    def test_force_color_styles_general_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            codex_home.joinpath("sessions").mkdir()
            session_id = "04040404-0505-0606-0707-080808080808"
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Color title"}],
            )

            buffer = StringIO()
            with patch.dict(os.environ, {"FORCE_COLOR": "1"}, clear=False):
                with redirect_stdout(buffer):
                    result = main(["list", "--codex-home", str(codex_home)])

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("\x1b[", output)
            self.assertIn("Color title", output)

    def test_encode_for_output_escapes_characters_unsupported_by_encoding(self) -> None:
        self.assertEqual(encode_for_output("Thread ✓", "cp1252"), r"Thread \u2713")
        self.assertEqual(encode_for_output("Thread ✓", "utf-8"), "Thread ✓")


if __name__ == "__main__":
    unittest.main()
