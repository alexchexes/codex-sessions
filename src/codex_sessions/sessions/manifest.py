import hashlib
import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from codex_sessions.sessions.rollout import FileFingerprint

MANIFEST_FILENAME = "codex-sessions-manifest-v1.json"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class ExportManifestEntry:
    relative_path: str
    session_id: str
    thread_name: str
    started_at: datetime | None
    updated_at: datetime | None
    fingerprint: FileFingerprint


@dataclass(frozen=True)
class ImportManifestEntry:
    relative_path: str
    session_id: str
    thread_name: str
    started_at: str | None
    updated_at: str | None
    fingerprint: FileFingerprint


def manifest_path_key(path: str | PurePosixPath | Path) -> str:
    path_text = path.as_posix() if isinstance(path, (PurePosixPath, Path)) else path
    return str(PurePosixPath(path_text.replace("\\", "/")))


def file_fingerprint_from_bytes(data: bytes) -> FileFingerprint:
    return FileFingerprint(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def manifest_entry_to_json(entry: ExportManifestEntry) -> dict[str, Any]:
    return {
        "path": entry.relative_path,
        "session_id": entry.session_id,
        "thread_name": entry.thread_name,
        "started_at": entry.started_at.isoformat() if entry.started_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        "size": entry.fingerprint.size,
        "sha256": entry.fingerprint.sha256,
    }


def export_manifest_bytes(entries: Iterable[ExportManifestEntry]) -> bytes:
    manifest = {
        "version": MANIFEST_VERSION,
        "rollouts": [manifest_entry_to_json(entry) for entry in entries],
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def parse_manifest_entry(raw_entry: Any) -> ImportManifestEntry:
    if not isinstance(raw_entry, dict):
        raise ValueError("manifest rollout entry is not an object")
    raw_path = raw_entry.get("path")
    session_id = raw_entry.get("session_id")
    thread_name = raw_entry.get("thread_name")
    size = raw_entry.get("size")
    sha256 = raw_entry.get("sha256")
    started_at = raw_entry.get("started_at")
    updated_at = raw_entry.get("updated_at")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("manifest rollout entry has no path")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"manifest entry {raw_path!r} has no session_id")
    if not isinstance(thread_name, str):
        raise ValueError(f"manifest entry {raw_path!r} has no thread_name")
    if not isinstance(size, int) or size < 0:
        raise ValueError(f"manifest entry {raw_path!r} has invalid size")
    if not isinstance(sha256, str) or not valid_sha256(sha256):
        raise ValueError(f"manifest entry {raw_path!r} has invalid sha256")
    if started_at is not None and not isinstance(started_at, str):
        raise ValueError(f"manifest entry {raw_path!r} has invalid started_at")
    if updated_at is not None and not isinstance(updated_at, str):
        raise ValueError(f"manifest entry {raw_path!r} has invalid updated_at")
    return ImportManifestEntry(
        relative_path=manifest_path_key(raw_path),
        session_id=session_id,
        thread_name=thread_name,
        started_at=started_at,
        updated_at=updated_at,
        fingerprint=FileFingerprint(size=size, sha256=sha256),
    )


def parse_manifest_entries(raw_manifest: Any) -> dict[str, ImportManifestEntry]:
    if not isinstance(raw_manifest, dict):
        raise ValueError("manifest root is not an object")
    if raw_manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("unsupported manifest version")
    raw_rollouts = raw_manifest.get("rollouts")
    if not isinstance(raw_rollouts, list):
        raise ValueError("manifest rollouts field is not a list")

    entries: dict[str, ImportManifestEntry] = {}
    for raw_entry in raw_rollouts:
        entry = parse_manifest_entry(raw_entry)
        if entry.relative_path in entries:
            raise ValueError(f"duplicate manifest path: {entry.relative_path}")
        entries[entry.relative_path] = entry
    return entries


def parse_manifest_text(
    text: str,
    *,
    source_label: str,
) -> tuple[dict[str, ImportManifestEntry], tuple[str, ...]]:
    try:
        raw_manifest = json.loads(text)
        return parse_manifest_entries(raw_manifest), ()
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, (
            f"Could not use export manifest {source_label}: {exc}. Falling back to hashing.",
        )


def read_directory_manifest(
    source_dir: Path,
) -> tuple[dict[str, ImportManifestEntry], tuple[str, ...]]:
    manifest_path = source_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return {}, ()
    try:
        return parse_manifest_text(
            manifest_path.read_text(encoding="utf-8"),
            source_label=str(manifest_path),
        )
    except (OSError, UnicodeDecodeError) as exc:
        return {}, (
            f"Could not read export manifest {manifest_path}: {exc}. Falling back to hashing.",
        )


def read_zip_manifest(
    archive: zipfile.ZipFile,
    zip_path: Path,
) -> tuple[dict[str, ImportManifestEntry], tuple[str, ...]]:
    try:
        data = archive.read(MANIFEST_FILENAME)
    except KeyError:
        return {}, ()
    except (OSError, zipfile.BadZipFile) as exc:
        return {}, (
            f"Could not read export manifest {zip_path}!{MANIFEST_FILENAME}: "
            f"{exc}. Falling back to hashing.",
        )
    return parse_manifest_text(
        data.decode("utf-8", errors="replace"),
        source_label=f"{zip_path}!{MANIFEST_FILENAME}",
    )
