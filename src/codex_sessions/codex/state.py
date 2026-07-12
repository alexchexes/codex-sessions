import errno
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.10 test environment
    import tomli as tomllib  # type: ignore[import-not-found]


SQLITE_HOME_ENV = "CODEX_SQLITE_HOME"


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
    # Verify before mutating session/index state so rollback never points at a bad backup.
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


def _path_from_sqlite_home_value(
    value: object, *, source: str, cwd: Path, allow_relative: bool
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CodexStateError(f"{source} must be a non-empty path string.")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        if not allow_relative:
            raise CodexStateError(f"{source} must be an absolute path.")
        path = cwd / path
    return path.resolve()


def resolve_codex_sqlite_home(
    codex_home: Path,
    explicit_sqlite_home: Path | None = None,
    *,
    cwd: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    """Resolve Codex SQLite home using Codex's config-before-environment precedence."""
    resolved_cwd = (cwd or Path.cwd()).resolve()
    if explicit_sqlite_home is not None:
        return _path_from_sqlite_home_value(
            str(explicit_sqlite_home),
            source="--sqlite-home",
            cwd=resolved_cwd,
            allow_relative=True,
        )

    config_path = codex_home / "config.toml"
    if config_path.exists():
        try:
            with config_path.open("rb") as src:
                config: dict[str, Any] = tomllib.load(src)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise CodexStateError(f"Could not read Codex config for sqlite_home: {exc}") from exc
        if "sqlite_home" in config:
            configured = _path_from_sqlite_home_value(
                config["sqlite_home"],
                source=f"{config_path}: sqlite_home",
                cwd=resolved_cwd,
                allow_relative=False,
            )
            return configured

    environment = os.environ if environ is None else environ
    raw_environment_home = environment.get(SQLITE_HOME_ENV)
    if raw_environment_home is not None and raw_environment_home.strip():
        return _path_from_sqlite_home_value(
            raw_environment_home,
            source=SQLITE_HOME_ENV,
            cwd=resolved_cwd,
            allow_relative=True,
        )
    return codex_home.resolve()


def state_cache_files(sqlite_home: Path) -> list[Path]:
    candidates = []
    for path in sqlite_home.glob("state_*.sqlite*"):
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
    # Resetting Codex state cache means moving live sqlite files aside, not deleting them.
    try:
        path.replace(backup_path)
    except OSError as exc:
        if not is_cross_device_error(exc):
            raise
        copy_state_cache_file_to_backup(path, backup_path)
    if not backup_path.exists():
        raise CodexStateError(f"Could not verify state cache backup: {backup_path}")
    return StateCacheBackup(original_path=path, backup_path=backup_path)


def is_cross_device_error(exc: OSError) -> bool:
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def files_have_same_content(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_file, right.open("rb") as right_file:
        while True:
            left_chunk = left_file.read(1024 * 1024)
            right_chunk = right_file.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def copy_state_cache_file_to_backup(path: Path, backup_path: Path) -> None:
    temp_backup_path = temp_path_for(backup_path)
    try:
        shutil.copy2(path, temp_backup_path)
        if not files_have_same_content(path, temp_backup_path):
            raise CodexStateError(f"Could not verify state cache backup: {backup_path}")
        temp_backup_path.replace(backup_path)
        try:
            path.unlink()
        except OSError:
            try:
                backup_path.unlink()
            except OSError:
                pass
            raise
    finally:
        try:
            temp_backup_path.unlink()
        except OSError:
            pass


def restore_state_cache_backup(backup: StateCacheBackup) -> None:
    try:
        backup.backup_path.replace(backup.original_path)
        return
    except OSError as exc:
        if not is_cross_device_error(exc):
            raise

    temp_original_path = temp_path_for(backup.original_path)
    try:
        shutil.copy2(backup.backup_path, temp_original_path)
        if not files_have_same_content(backup.backup_path, temp_original_path):
            raise CodexStateError(
                f"Could not verify restored state cache file: {backup.original_path}"
            )
        temp_original_path.replace(backup.original_path)
        try:
            backup.backup_path.unlink()
        except OSError:
            # The live file is safely restored; retaining the extra backup is preferable
            # to removing the restored database because backup cleanup was blocked.
            pass
    finally:
        try:
            temp_original_path.unlink()
        except OSError:
            pass


def restore_state_cache_backups(backups: Sequence[StateCacheBackup]) -> None:
    for backup in reversed(backups):
        if backup.backup_path.exists() and not backup.original_path.exists():
            restore_state_cache_backup(backup)


def reset_codex_state_cache(sqlite_home: Path, backup_dir: Path) -> tuple[StateCacheBackup, ...]:
    backups: list[StateCacheBackup] = []
    for path in state_cache_files(sqlite_home):
        try:
            backups.append(backup_state_cache_file(path, backup_dir))
        except OSError as exc:
            restore_state_cache_backups(backups)
            raise CodexStateError(
                "Could not reset Codex state cache file:\n"
                f"  {path}\n"
                f"  {exc}\n"
                "Close all Codex writers and retry."
            ) from exc
        except CodexStateError:
            restore_state_cache_backups(backups)
            raise
    return tuple(backups)


def reset_codex_state_cache_with_backup(
    sqlite_home: Path, *, backup_home: Path | None = None
) -> tuple[StateCacheBackup, ...]:
    backup_dir = backup_dir_for(backup_home or sqlite_home, backup_label())
    try:
        return reset_codex_state_cache(sqlite_home, backup_dir)
    finally:
        remove_backup_dir_if_empty(backup_dir)
