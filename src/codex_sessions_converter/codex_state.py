import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class CodexStateError(Exception):
    pass


@dataclass(frozen=True)
class StateCacheBackup:
    original_path: Path
    backup_path: Path


def backup_label() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{os.getpid()}"


def backup_dir_for(codex_home: Path, label: str) -> Path:
    return codex_home / "backups" / "codex-sessions" / label


def backup_path_for(path: Path, backup_dir: Path) -> Path:
    return backup_dir / path.name


def temp_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_path_for(path, backup_dir)
    shutil.copy2(path, backup_path)
    if backup_path.read_bytes() != path.read_bytes():
        try:
            backup_path.unlink()
        except OSError:
            pass
        raise CodexStateError(f"Could not verify backup: {backup_path}")
    return backup_path


def backup_session_index(index_path: Path, backup_dir: Path) -> Path | None:
    try:
        return backup_file(index_path, backup_dir)
    except CodexStateError as exc:
        raise CodexStateError(f"Could not verify session index backup: {backup_dir}") from exc


def restore_file_backup(path: Path, backup_path: Path | None) -> None:
    if backup_path is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    shutil.copy2(backup_path, path)
    backup_path.unlink()


def restore_session_index_backup(index_path: Path, backup_path: Path | None) -> None:
    restore_file_backup(index_path, backup_path)


def remove_backup_dir_if_empty(backup_dir: Path) -> None:
    try:
        backup_dir.rmdir()
    except OSError:
        pass


def state_cache_files(codex_home: Path) -> list[Path]:
    candidates = []
    for path in codex_home.glob("state_*.sqlite*"):
        if path.is_file() and is_live_state_cache_file(path):
            candidates.append(path)
    return sorted(candidates)


def is_live_state_cache_file(path: Path) -> bool:
    name = path.name
    if ".backup-" in name:
        return False
    return (
        name.endswith(".sqlite")
        or name.endswith(".sqlite-shm")
        or name.endswith(".sqlite-wal")
        or name.endswith(".sqlite-journal")
    )


def backup_state_cache_file(path: Path, backup_dir: Path) -> StateCacheBackup:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_path_for(path, backup_dir)
    path.replace(backup_path)
    if not backup_path.exists():
        raise CodexStateError(f"Could not verify state cache backup: {backup_path}")
    return StateCacheBackup(original_path=path, backup_path=backup_path)


def restore_state_cache_backups(backups: Sequence[StateCacheBackup]) -> None:
    for backup in reversed(backups):
        if backup.backup_path.exists() and not backup.original_path.exists():
            backup.backup_path.replace(backup.original_path)


def reset_codex_state_cache(codex_home: Path, backup_dir: Path) -> tuple[StateCacheBackup, ...]:
    backups: list[StateCacheBackup] = []
    for path in state_cache_files(codex_home):
        try:
            backups.append(backup_state_cache_file(path, backup_dir))
        except OSError as exc:
            restore_state_cache_backups(backups)
            raise CodexStateError(
                f"Could not reset Codex state cache file {path}: {exc}. "
                "Close all Codex sessions and retry."
            ) from exc
        except CodexStateError:
            restore_state_cache_backups(backups)
            raise
    return tuple(backups)
