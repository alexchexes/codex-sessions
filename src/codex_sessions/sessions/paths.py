import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_sessions.errors import CliError
from codex_sessions.sessions.cache import (
    cached_session_metadata,
    prune_missing_session_cache_entries,
    read_session_cache,
    session_cache_entry,
    session_cache_key,
    session_cache_path,
    write_session_cache,
)
from codex_sessions.sessions.files import (
    discover_session_files,
    discover_session_paths,
    format_session_file_path,
    session_file_metadata,
)
from codex_sessions.sessions.index import (
    is_session_id,
    normalize_session_id,
    resolve_session_index_record,
    session_index_record_id,
    session_index_records,
)
from codex_sessions.sessions.rollout import FileFingerprint

LATEST_TARGET = "latest"


@dataclass(frozen=True)
class ConversionInput:
    path: Path
    output_stem: str | None


@dataclass(frozen=True)
class LatestSessionSortResult:
    sort_key: tuple[int, float]
    cache_key: str
    cache_entry: dict[str, Any] | None


def normalize_output_format(output_format: str | None) -> str | None:
    if output_format == "markdown":
        return "md"
    return output_format


def infer_output_format(args: argparse.Namespace) -> str:
    if args.md:
        return "md"
    if args.yaml:
        return "yaml"
    explicit_format = normalize_output_format(args.format)
    if explicit_format:
        return explicit_format
    if args.output and args.output.suffix.lower() in {".md", ".markdown"}:
        return "md"
    return "yaml"


def output_filename(input_path: Path, output_format: str = "yaml", stem: str | None = None) -> str:
    suffix = ".md" if output_format == "md" else ".yaml"
    if stem:
        return f"{stem}{suffix}"
    if input_path.suffix.lower() == ".jsonl":
        return input_path.with_suffix(suffix).name
    return input_path.with_suffix(input_path.suffix + suffix).name


def default_output_path(
    input_path: Path,
    codex_home: Path,
    output_format: str = "yaml",
    stem: str | None = None,
) -> Path:
    output_name = output_filename(input_path, output_format, stem)
    try:
        relative_input = input_path.resolve().relative_to(codex_home.resolve())
    except ValueError:
        return codex_home / "tmp" / output_name
    # Mirror Codex's session date folders under tmp so converted files stay discoverable.
    return (codex_home / "tmp" / relative_input).with_name(output_name)


def resolve_output_path(
    output_arg: Path | None,
    input_path: Path,
    codex_home: Path,
    output_format: str,
    stem: str | None = None,
) -> Path:
    if output_arg is None:
        return default_output_path(input_path, codex_home, output_format, stem).resolve()

    expanded_output = output_arg.expanduser()
    if expanded_output.exists() and expanded_output.is_dir():
        return (expanded_output / output_filename(input_path, output_format, stem)).resolve()
    return expanded_output.resolve()


def resolve_session_id(
    session_id: str,
    codex_home: Path,
    *,
    sessions_dir: Path | None = None,
) -> Path:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    normalized_id = normalize_session_id(session_id)
    matches = [
        session_file.path
        for session_file in discover_session_files(resolved_sessions_dir)
        if (
            session_file.session_id
            and normalize_session_id(session_file.session_id) == normalized_id
        )
    ]
    if not matches:
        raise CliError(f"No Codex session found for ID: {session_id}")
    if len(matches) > 1:
        rendered_matches = ", ".join(
            format_session_file_path(path, resolved_sessions_dir) for path in matches
        )
        raise CliError(
            f"Multiple Codex session files found for ID {session_id}: {rendered_matches}"
        )
    return matches[0].resolve()


def resolve_latest_session(codex_home: Path, *, sessions_dir: Path | None = None) -> Path:
    resolved_sessions_dir = sessions_dir or codex_home / "sessions"
    matches = discover_session_paths(resolved_sessions_dir)
    if not matches:
        raise CliError("No Codex session rollout files found.")

    metadata_cache_path = session_cache_path(codex_home)
    metadata_cache_entries = read_session_cache(metadata_cache_path)
    metadata_cache_dirty = prune_missing_session_cache_entries(metadata_cache_entries)
    latest_path: Path | None = None
    latest_key: tuple[int, float] | None = None

    for path in matches:
        result = latest_session_sort_result(path, metadata_cache_entries)
        if result.cache_entry is not None:
            metadata_cache_entries[result.cache_key] = result.cache_entry
            metadata_cache_dirty = True
        if latest_key is None or result.sort_key > latest_key:
            latest_path = path
            latest_key = result.sort_key

    if metadata_cache_dirty:
        try:
            write_session_cache(metadata_cache_path, metadata_cache_entries)
        except OSError:
            pass

    assert latest_path is not None
    return latest_path.resolve()


def latest_session_sort_key(path: Path) -> tuple[int, float]:
    return latest_session_sort_result(path, None).sort_key


def latest_session_sort_result(
    path: Path, metadata_cache_entries: dict[str, Any] | None
) -> LatestSessionSortResult:
    stat_result = path.stat()
    cache_key = session_cache_key(path)
    metadata = (
        cached_session_metadata(metadata_cache_entries.get(cache_key), path, stat_result)
        if metadata_cache_entries is not None
        else None
    )
    if metadata is not None:
        if metadata.ended_at is not None or metadata.timestamps_scanned:
            timestamp = metadata.ended_at or metadata.started_at
            if timestamp is not None:
                return LatestSessionSortResult((1, timestamp.timestamp()), cache_key, None)
            return LatestSessionSortResult((0, stat_result.st_mtime), cache_key, None)

    fingerprint = (
        FileFingerprint(size=metadata.size, sha256=metadata.sha256)
        if metadata is not None and metadata.sha256 is not None
        else None
    )
    session_id, started_at, ended_at = session_file_metadata(path, include_ended_at=True)
    timestamp = ended_at or started_at
    updated_stat_result = path.stat()
    cache_entry = None
    if (
        metadata_cache_entries is not None
        and updated_stat_result.st_size == stat_result.st_size
        and updated_stat_result.st_mtime_ns == stat_result.st_mtime_ns
    ):
        cache_entry = session_cache_entry(
            path,
            updated_stat_result,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended_at,
            timestamps_scanned=True,
            fingerprint=fingerprint,
        )
    if timestamp is not None:
        return LatestSessionSortResult((1, timestamp.timestamp()), cache_key, cache_entry)
    return LatestSessionSortResult((0, updated_stat_result.st_mtime), cache_key, cache_entry)


def looks_like_missing_file_path(raw_input: Path) -> bool:
    input_text = str(raw_input)
    return raw_input.suffix != "" or "/" in input_text or "\\" in input_text


def resolve_session_title(
    title: str,
    codex_home: Path,
    *,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
) -> ConversionInput:
    try:
        _, record = resolve_session_index_record(
            session_index_records(session_index_path or codex_home / "session_index.jsonl"),
            title,
        )
    except ValueError as exc:
        raise CliError(str(exc)) from exc

    session_id = session_index_record_id(record)
    if session_id is None:
        raise CliError(f"session_index.jsonl entry for title {title!r} has no ID.")
    return ConversionInput(
        path=resolve_session_id(session_id, codex_home, sessions_dir=sessions_dir),
        output_stem=normalize_session_id(session_id),
    )


def resolve_conversion_input(
    raw_input: Path,
    codex_home: Path,
    *,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
) -> ConversionInput:
    input_text = str(raw_input)
    if input_text.lower() == LATEST_TARGET:
        latest_path = resolve_latest_session(codex_home, sessions_dir=sessions_dir)
        return ConversionInput(path=latest_path, output_stem=None)

    if is_session_id(input_text):
        return ConversionInput(
            path=resolve_session_id(input_text, codex_home, sessions_dir=sessions_dir),
            output_stem=normalize_session_id(input_text),
        )

    expanded_input = raw_input.expanduser()
    if expanded_input.exists():
        if not expanded_input.is_file():
            raise CliError(f"Input path is not a file: {raw_input}")
        return ConversionInput(path=expanded_input.resolve(), output_stem=None)

    codex_home_input = codex_home / raw_input
    if not raw_input.is_absolute() and codex_home_input.exists():
        if not codex_home_input.is_file():
            raise CliError(f"Input path is not a file: {raw_input}")
        return ConversionInput(path=codex_home_input.resolve(), output_stem=None)

    sessions_dir_input = (
        (sessions_dir / raw_input) if sessions_dir and not raw_input.is_absolute() else None
    )
    if sessions_dir_input is not None and sessions_dir_input.exists():
        if not sessions_dir_input.is_file():
            raise CliError(f"Input path is not a file: {raw_input}")
        return ConversionInput(path=sessions_dir_input.resolve(), output_stem=None)

    try:
        return resolve_session_title(
            input_text,
            codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
        )
    except CliError as exc:
        if not looks_like_missing_file_path(raw_input):
            raise
        raise CliError(f"Input file not found: {raw_input}") from exc


def resolve_session_target_paths(
    targets: Sequence[str],
    codex_home: Path,
    *,
    session_index_path: Path | None = None,
    sessions_dir: Path | None = None,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        resolved = resolve_conversion_input(
            Path(target),
            codex_home,
            session_index_path=session_index_path,
            sessions_dir=sessions_dir,
        ).path
        normalized_path = os.path.normcase(str(resolved.resolve()))
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        paths.append(resolved)
    return tuple(paths)
