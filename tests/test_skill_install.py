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
            root = Path(tmpdir)
            skills_dir = root / ".agents" / "skills"
            previous_skills_dir = root / ".codex" / "skills"
            old_skill_paths = (
                skills_dir / "read-codex-session",
                previous_skills_dir / "codex-sessions",
                previous_skills_dir / "read-codex-session",
            )
            for old_skill_path in old_skill_paths:
                old_skill_path.mkdir(parents=True)
                old_skill_path.joinpath("SKILL.md").write_text("old skill", encoding="utf-8")

            result = install_codex_skill(
                skills_dir,
                previous_skills_dir=previous_skills_dir,
            )

            installed_skill = skills_dir / "codex-sessions"
            self.assertEqual(result.destination, installed_skill)
            self.assertEqual(result.removed_obsolete_paths, old_skill_paths)
            self.assertTrue(all(not path.exists() for path in old_skill_paths))
            self.assertTrue(installed_skill.joinpath("SKILL.md").is_file())
            self.assertFalse(installed_skill.joinpath("scripts").exists())
            self.assertIn(
                "name: codex-sessions",
                installed_skill.joinpath("SKILL.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
