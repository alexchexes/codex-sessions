from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from codex_sessions.core.json_streams import iter_jsonl_objects
from codex_sessions.sessions.index import normalize_session_id
from codex_sessions.sessions.rollout import FileFingerprint, file_fingerprint


class RolloutHistoryRelation(str, Enum):
    IDENTICAL = "identical"
    EQUIVALENT = "equivalent"
    INCOMING_AHEAD = "incoming_ahead"
    LOCAL_AHEAD = "local_ahead"
    DIVERGED = "diverged"


@dataclass(frozen=True)
class RolloutHistoryComparison:
    relation: RolloutHistoryRelation
    common_comparable_records: int | None
    local_tail_comparable_records: int
    incoming_tail_comparable_records: int
    local_divergence_record: dict[str, Any] | None = None
    incoming_divergence_record: dict[str, Any] | None = None


def is_thread_name_updated_record(record: dict[str, Any]) -> bool:
    payload = record.get("payload")
    return (
        record.get("type") == "event_msg"
        and isinstance(payload, dict)
        and payload.get("type") == "thread_name_updated"
    )


def comparable_rollout_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records that define history ancestry; title-only changes are ignored."""
    for _, record in iter_jsonl_objects(path):
        if not is_thread_name_updated_record(record):
            yield record


def compare_rollout_histories(
    local_path: Path,
    incoming_path: Path,
    *,
    local_session_id: str,
    incoming_session_id: str,
    local_fingerprint: FileFingerprint | None = None,
    incoming_fingerprint: FileFingerprint | None = None,
) -> RolloutHistoryComparison:
    if normalize_session_id(local_session_id) != normalize_session_id(incoming_session_id):
        raise ValueError(
            "Cannot compare rollout histories for different session IDs: "
            f"{local_session_id} != {incoming_session_id}"
        )

    resolved_local_fingerprint = local_fingerprint or file_fingerprint(local_path)
    resolved_incoming_fingerprint = incoming_fingerprint or file_fingerprint(incoming_path)
    if resolved_local_fingerprint == resolved_incoming_fingerprint:
        # Exact file equality is stronger than semantic equivalence and avoids parsing.
        return RolloutHistoryComparison(
            relation=RolloutHistoryRelation.IDENTICAL,
            common_comparable_records=None,
            local_tail_comparable_records=0,
            incoming_tail_comparable_records=0,
        )

    return compare_comparable_rollout_histories(local_path, incoming_path)


def compare_comparable_rollout_histories(
    local_path: Path, incoming_path: Path
) -> RolloutHistoryComparison:
    local_records = comparable_rollout_records(local_path)
    incoming_records = comparable_rollout_records(incoming_path)
    common_records = 0

    while True:
        local_record = next_record(local_records)
        incoming_record = next_record(incoming_records)
        if local_record is None and incoming_record is None:
            return RolloutHistoryComparison(
                relation=RolloutHistoryRelation.EQUIVALENT,
                common_comparable_records=common_records,
                local_tail_comparable_records=0,
                incoming_tail_comparable_records=0,
            )
        if local_record is None:
            return RolloutHistoryComparison(
                relation=RolloutHistoryRelation.INCOMING_AHEAD,
                common_comparable_records=common_records,
                local_tail_comparable_records=0,
                incoming_tail_comparable_records=count_tail_records(
                    incoming_record, incoming_records
                ),
            )
        if incoming_record is None:
            return RolloutHistoryComparison(
                relation=RolloutHistoryRelation.LOCAL_AHEAD,
                common_comparable_records=common_records,
                local_tail_comparable_records=count_tail_records(local_record, local_records),
                incoming_tail_comparable_records=0,
            )
        if local_record != incoming_record:
            return RolloutHistoryComparison(
                relation=RolloutHistoryRelation.DIVERGED,
                common_comparable_records=common_records,
                local_tail_comparable_records=count_tail_records(local_record, local_records),
                incoming_tail_comparable_records=count_tail_records(
                    incoming_record, incoming_records
                ),
                local_divergence_record=local_record,
                incoming_divergence_record=incoming_record,
            )
        common_records += 1


def next_record(records: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(records)
    except StopIteration:
        return None


def count_tail_records(
    first_record: dict[str, Any] | None, records: Iterator[dict[str, Any]]
) -> int:
    return (1 if first_record is not None else 0) + sum(1 for _ in records)
