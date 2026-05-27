import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def iter_jsonl_objects(input_path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with input_path.open("r", encoding="utf-8") as src:
        for line_number, raw_line in enumerate(src, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {input_path}: {exc}"
                ) from exc
            yield line_number, obj


def iter_concatenated_json_objects(input_path: Path) -> Iterable[tuple[int, Any]]:
    """Read JSONL that may contain several adjacent JSON objects on one physical line."""
    decoder = json.JSONDecoder()
    with input_path.open("r", encoding="utf-8") as src:
        for line_number, raw_line in enumerate(src, start=1):
            remaining = raw_line.strip()
            while remaining:
                try:
                    obj, end = decoder.raw_decode(remaining)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON on line {line_number} of {input_path}: {exc}"
                    ) from exc
                yield line_number, obj
                remaining = remaining[end:].lstrip()
