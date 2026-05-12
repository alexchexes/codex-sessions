import re
from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    fractional = re.search(r"\.(\d+)(?=[+-]\d\d:?\d\d$|$)", text)
    if fractional and len(fractional.group(1)) > 6:
        text = text[: fractional.start(1) + 6] + text[fractional.end(1) :]

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
