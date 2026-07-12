import errno
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
    offer_state_database_rebuild,
    prompt_state_database_rebuild,
)
from codex_sessions.codex.state import (  # noqa: E402
    CodexStateError,
    resolve_codex_sqlite_home,
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


class CliStateCacheTests(unittest.TestCase):
    def test_reset_state_cache_command_backs_up_live_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_wal = codex_home / "state_5.sqlite-wal"
            state_db.write_text("state", encoding="utf-8")
            state_wal.write_text("wal", encoding="utf-8")

            buffer = StringIO()
            with redirect_stdout(buffer):
                result = main(["reset-state-cache", "--codex-home", str(codex_home), "--yes"])

            output = buffer.getvalue()
            backup_dirs = sorted((codex_home / "backups" / "codex-sessions").iterdir())
            self.assertEqual(result, 0)
            self.assertIn("State database rebuild OK.", output)
            self.assertIn("Backups:", output)
            self.assertFalse(state_db.exists())
            self.assertFalse(state_wal.exists())
            self.assertEqual(len(backup_dirs), 1)
            self.assertEqual((backup_dirs[0] / state_db.name).read_text(encoding="utf-8"), "state")
            self.assertEqual((backup_dirs[0] / state_wal.name).read_text(encoding="utf-8"), "wal")

    def test_reset_state_cache_command_fails_when_reset_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)

            with patch(
                "codex_sessions.cli.reset_codex_state_cache_with_backup",
                side_effect=OSError("locked"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    main(["reset-state-cache", "--codex-home", str(codex_home), "--yes"])

            self.assertIn("locked", str(raised.exception))

    def test_reset_state_cache_requires_confirmation_when_not_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                main(["reset-state-cache", "--codex-home", str(codex_home)])

            self.assertIn("Close all Codex writers", str(raised.exception))
            self.assertEqual(state_db.read_text(encoding="utf-8"), "state")

    def test_reset_state_cache_uses_explicit_sqlite_home_and_codex_backup_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sqlite_home = root / "sqlite"
            codex_home.mkdir()
            sqlite_home.mkdir()
            state_db = sqlite_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with redirect_stdout(StringIO()):
                result = main(
                    [
                        "reset-state-cache",
                        "--codex-home",
                        str(codex_home),
                        "--sqlite-home",
                        str(sqlite_home),
                        "--yes",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertFalse(state_db.exists())
            backups = list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "state")

    def test_reset_state_cache_supports_cross_filesystem_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sqlite_home = root / "sqlite"
            codex_home.mkdir()
            sqlite_home.mkdir()
            state_db = sqlite_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")
            original_replace = Path.replace

            def replace_with_cross_device_error(source: Path, target: Path) -> Path:
                if source == state_db:
                    raise OSError(errno.EXDEV, "simulated cross-filesystem move")
                return original_replace(source, target)

            with (
                patch.object(Path, "replace", replace_with_cross_device_error),
                redirect_stdout(StringIO()),
            ):
                result = main(
                    [
                        "reset-state-cache",
                        "--codex-home",
                        str(codex_home),
                        "--sqlite-home",
                        str(sqlite_home),
                        "--yes",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertFalse(state_db.exists())
            backups = list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "state")

    def test_cross_filesystem_backup_rolls_back_when_later_file_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            sqlite_home = root / "sqlite"
            codex_home.mkdir()
            sqlite_home.mkdir()
            state_db = sqlite_home / "state_5.sqlite"
            state_wal = sqlite_home / "state_5.sqlite-wal"
            state_db.write_text("state", encoding="utf-8")
            state_wal.write_text("wal", encoding="utf-8")
            original_replace = Path.replace

            def replace_with_cross_device_and_lock(source: Path, target: Path) -> Path:
                target_path = Path(target)
                if source == state_wal:
                    raise PermissionError("simulated lock")
                if source.name == state_db.name and source.parent != target_path.parent:
                    raise OSError(errno.EXDEV, "simulated cross-filesystem move")
                return original_replace(source, target)

            with (
                patch.object(Path, "replace", replace_with_cross_device_and_lock),
                redirect_stdout(StringIO()),
            ):
                with self.assertRaises(SystemExit) as raised:
                    main(
                        [
                            "reset-state-cache",
                            "--codex-home",
                            str(codex_home),
                            "--sqlite-home",
                            str(sqlite_home),
                            "--yes",
                        ]
                    )

            self.assertIn("simulated lock", str(raised.exception))
            self.assertEqual(state_db.read_text(encoding="utf-8"), "state")
            self.assertEqual(state_wal.read_text(encoding="utf-8"), "wal")
            self.assertEqual(list(codex_home.glob("backups/codex-sessions/**/*")), [])

    def test_reset_state_cache_respects_codex_home_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            with (
                patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=True),
                redirect_stdout(StringIO()),
            ):
                result = main(["reset-state-cache", "--yes"])

            self.assertEqual(result, 0)
            self.assertFalse(state_db.exists())

    def test_resolve_sqlite_home_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()
            configured_home = root / "configured"
            environment_home = root / "environment"
            explicit_home = root / "explicit"
            (codex_home / "config.toml").write_text(
                f'sqlite_home = "{configured_home.as_posix()}"\n', encoding="utf-8"
            )

            self.assertEqual(
                resolve_codex_sqlite_home(
                    codex_home,
                    cwd=root,
                    environ={"CODEX_SQLITE_HOME": str(environment_home)},
                ),
                configured_home.resolve(),
            )
            self.assertEqual(
                resolve_codex_sqlite_home(
                    codex_home,
                    explicit_home,
                    cwd=root,
                    environ={"CODEX_SQLITE_HOME": str(environment_home)},
                ),
                explicit_home.resolve(),
            )

    def test_resolve_sqlite_home_uses_relative_environment_path_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex"
            codex_home.mkdir()

            self.assertEqual(
                resolve_codex_sqlite_home(
                    codex_home,
                    cwd=root,
                    environ={"CODEX_SQLITE_HOME": "relative-sqlite"},
                ),
                (root / "relative-sqlite").resolve(),
            )

    def test_resolve_sqlite_home_rejects_relative_config_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text(
                'sqlite_home = "relative-sqlite"\n', encoding="utf-8"
            )

            with self.assertRaises(CodexStateError) as raised:
                resolve_codex_sqlite_home(codex_home, cwd=codex_home, environ={})

            self.assertIn("must be an absolute path", str(raised.exception))

    def test_state_database_rebuild_prompt_defaults_to_no(self) -> None:
        with patch("builtins.input", return_value=""):
            self.assertFalse(prompt_state_database_rebuild())

    def test_interactive_state_database_rebuild_can_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")

            buffer = StringIO()
            with (
                patch(
                    "codex_sessions.cli.can_prompt_state_cache_reset_interactively",
                    return_value=True,
                ),
                patch(
                    "codex_sessions.cli.prompt_state_database_rebuild", return_value=True
                ) as prompt,
                redirect_stdout(buffer),
            ):
                offer_state_database_rebuild(
                    codex_home,
                    sqlite_home_override=None,
                    non_interactive=False,
                    offer_reset=True,
                )

            output = buffer.getvalue()
            prompt.assert_called_once_with("All Codex writers are closed; rebuild now? [y/N] ")
            self.assertIn("Optional Codex state database rebuild:", output)
            self.assertIn("State database rebuild OK.", output)
            self.assertFalse(state_db.exists())
            state_backups = list(codex_home.glob("backups/codex-sessions/*/state_5.sqlite"))
            self.assertEqual(len(state_backups), 1)
            self.assertEqual(state_backups[0].read_text(encoding="utf-8"), "state")

    def test_non_interactive_state_database_rebuild_never_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            state_db = codex_home / "state_5.sqlite"
            state_db.write_text("state", encoding="utf-8")
            relative_sqlite_home = Path("deferred-relative-sqlite")
            resolved_sqlite_home = (Path.cwd() / relative_sqlite_home).resolve()

            buffer = StringIO()
            with (
                patch("codex_sessions.cli.prompt_state_database_rebuild") as prompt,
                redirect_stdout(buffer),
            ):
                offer_state_database_rebuild(
                    codex_home,
                    sqlite_home_override=relative_sqlite_home,
                    non_interactive=True,
                    offer_reset=True,
                )

            prompt.assert_not_called()
            output = buffer.getvalue()
            self.assertIn("State database rebuild skipped.", output)
            self.assertIn(f'--sqlite-home "{resolved_sqlite_home}"', output)
            self.assertEqual(state_db.read_text(encoding="utf-8"), "state")


if __name__ == "__main__":
    unittest.main()
