from __future__ import annotations

from collections import Counter

from .domain import ParsedRecord


def validate_records(records: list[ParsedRecord], profile_name: str) -> dict[str, object]:
    fingerprints = [
        tuple(
            (key, str(value))
            for key, value in record.values.items()
            if not key.startswith("来源") and not key.startswith("_")
        )
        for record in records
    ]
    duplicate_count = sum(count - 1 for count in Counter(fingerprints).values() if count > 1)
    return {
        "profile": profile_name,
        "record_count": len(records),
        "review_count": sum(record.needs_review for record in records),
        "duplicate_count": duplicate_count,
        "valid": bool(records) and duplicate_count == 0,
    }
