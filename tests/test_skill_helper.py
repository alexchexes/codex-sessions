import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.skills.install import install_codex_skill  # noqa: E402

HELPER = ROOT / "skills" / "codex-sessions" / "scripts" / "prepare_session_markdown.py"


class SkillHelperTests(unittest.TestCase):
    def test_install_codex_skill_replaces_legacy_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            legacy_skill = codex_home / "skills" / "read-codex-session"
            legacy_skill.mkdir(parents=True)
            legacy_skill.joinpath("SKILL.md").write_text("old skill", encoding="utf-8")

            result = install_codex_skill(codex_home)

            installed_skill = codex_home / "skills" / "codex-sessions"
            self.assertEqual(result.destination, installed_skill)
            self.assertEqual(result.removed_legacy_path, legacy_skill)
            self.assertFalse(legacy_skill.exists())
            self.assertTrue(
                installed_skill.joinpath("scripts", "prepare_session_markdown.py").is_file()
            )
            self.assertIn(
                "name: codex-sessions",
                installed_skill.joinpath("SKILL.md").read_text(encoding="utf-8"),
            )

    def test_prepare_session_markdown_uses_current_cli_output_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            session_id = "019f0000-0000-7000-8000-000000000001"
            rollout_path = (
                codex_home
                / "sessions"
                / "2026"
                / "05"
                / "27"
                / f"rollout-2026-05-27T10-00-00-{session_id}.jsonl"
            )
            rollout_path.parent.mkdir(parents=True)
            write_jsonl(
                rollout_path,
                [
                    {"type": "session_meta", "payload": {"id": session_id}},
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": "hello from skill helper",
                        },
                    },
                ],
            )

            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    session_id,
                    "--codex-home",
                    str(codex_home),
                    "--command",
                    "codex-sessions-command-that-does-not-exist",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output_path = Path(result.stdout.strip().splitlines()[-1])
            self.assertTrue(output_path.exists())
            self.assertIn("hello from skill helper", output_path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
