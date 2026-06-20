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
    removed_obsolete_paths: tuple[Path, ...]


SkillSource: TypeAlias = Path | Traversable


def install_codex_skill(
    skills_dir: Path, *, previous_skills_dir: Path | None = None
) -> InstallSkillResult:
    destination = skills_dir / SKILL_NAME
    temp_destination = skills_dir / f".{SKILL_NAME}.tmp-{os.getpid()}"
    source = bundled_skill_source()

    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        if temp_destination.exists():
            shutil.rmtree(temp_destination)
        copy_skill_tree(source, temp_destination)

        replaced_existing = destination.exists()
        if replaced_existing:
            shutil.rmtree(destination)
        temp_destination.replace(destination)

        obsolete_paths = [skills_dir / LEGACY_SKILL_NAME]
        if previous_skills_dir is not None:
            obsolete_paths.extend(
                [
                    previous_skills_dir / SKILL_NAME,
                    previous_skills_dir / LEGACY_SKILL_NAME,
                ]
            )

        removed_obsolete_paths = []
        for obsolete_path in dict.fromkeys(obsolete_paths):
            if obsolete_path == destination or not obsolete_path.exists():
                continue
            shutil.rmtree(obsolete_path)
            removed_obsolete_paths.append(obsolete_path)
    except OSError as exc:
        if temp_destination.exists():
            shutil.rmtree(temp_destination, ignore_errors=True)
        raise CliError(f"Failed to install Codex skill: {exc}") from exc

    return InstallSkillResult(
        skill_name=SKILL_NAME,
        destination=destination,
        replaced_existing=replaced_existing,
        removed_obsolete_paths=tuple(removed_obsolete_paths),
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
