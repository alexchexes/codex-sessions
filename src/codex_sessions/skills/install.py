import os
import shutil
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TypeAlias

if sys.version_info >= (3, 11):
    from importlib.resources.abc import Traversable
else:
    from importlib.abc import Traversable

from codex_sessions.errors import CliError

SKILL_NAME = "codex-sessions"
LEGACY_SKILL_NAME = "read-codex-session"


@dataclass(frozen=True)
class InstallSkillResult:
    skill_name: str
    destination: Path
    replaced_existing: bool
    removed_legacy_path: Path | None


SkillSource: TypeAlias = Path | Traversable


def install_codex_skill(codex_home: Path) -> InstallSkillResult:
    skills_root = codex_home / "skills"
    destination = skills_root / SKILL_NAME
    temp_destination = skills_root / f".{SKILL_NAME}.tmp-{os.getpid()}"
    source = bundled_skill_source()

    try:
        skills_root.mkdir(parents=True, exist_ok=True)
        if temp_destination.exists():
            shutil.rmtree(temp_destination)
        copy_skill_tree(source, temp_destination)

        replaced_existing = destination.exists()
        if replaced_existing:
            shutil.rmtree(destination)
        temp_destination.replace(destination)

        legacy_path = skills_root / LEGACY_SKILL_NAME
        removed_legacy_path = None
        if legacy_path.exists():
            shutil.rmtree(legacy_path)
            removed_legacy_path = legacy_path
    except OSError as exc:
        if temp_destination.exists():
            shutil.rmtree(temp_destination, ignore_errors=True)
        raise CliError(f"Failed to install Codex skill: {exc}") from exc

    return InstallSkillResult(
        skill_name=SKILL_NAME,
        destination=destination,
        replaced_existing=replaced_existing,
        removed_legacy_path=removed_legacy_path,
    )


def bundled_skill_source() -> SkillSource:
    checkout_source = checkout_skill_source()
    if checkout_source.joinpath("SKILL.md").is_file():
        return checkout_source

    packaged_source = (
        resources.files("codex_sessions.skills").joinpath("bundled").joinpath(SKILL_NAME)
    )
    if packaged_source.joinpath("SKILL.md").is_file():
        return packaged_source

    raise CliError(
        "Bundled Codex skill was not found. Reinstall codex-sessions or run from a full checkout."
    )


def checkout_skill_source() -> Path:
    return Path(__file__).resolve().parents[3] / "skills" / SKILL_NAME


def copy_skill_tree(source: SkillSource, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        if child.name == "__pycache__":
            continue
        target = destination / child.name
        if child.is_dir():
            copy_skill_tree(child, target)
        elif child.is_file():
            target.write_bytes(child.read_bytes())
