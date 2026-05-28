import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codex_sessions.skills.install import install_codex_skill  # noqa: E402


class SkillInstallTests(unittest.TestCase):
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
            self.assertTrue(installed_skill.joinpath("SKILL.md").is_file())
            self.assertFalse(installed_skill.joinpath("scripts").exists())
            self.assertIn(
                "name: codex-sessions",
                installed_skill.joinpath("SKILL.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
