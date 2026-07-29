from __future__ import annotations

from collections import Counter

from .domain import ParsedRecord


def _issue_mentions_field(record: ParsedRecord, field: str) -> bool:
    return any(field in issue for issue in record.issues)


def _issue_kind(message: str) -> str:
    return message.split("（", 1)[0].split("：", 1)[0].strip()


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
    silent_critical_risks: list[dict[str, object]] = []
    for index, record in enumerate(records, start=1):
        evidence = record.values.get("_field_evidence", {})
        if not isinstance(evidence, dict):
            continue
        for field, details in evidence.items():
            if not isinstance(details, dict) or details.get("status") != "unresolved":
                continue
            if not _issue_mentions_field(record, str(field)):
                silent_critical_risks.append(
                    {
                        "record": index,
                        "field": str(field),
                        "source": record.values.get("来源文件", ""),
                        "page": record.values.get("来源页", ""),
                    }
                )
    data_count = sum(not record.needs_review for record in records)
    review_count = sum(record.needs_review for record in records)
    audit_count = sum(record.needs_audit for record in records)
    low_confidence_count = sum(
        isinstance(record.values.get("关键字段最低OCR置信度"), (int, float))
        and float(record.values["关键字段最低OCR置信度"]) < 0.88
        for record in records
    )
    structurally_valid = bool(records) and duplicate_count == 0
    release_blockers: list[str] = []
    if not records:
        release_blockers.append("没有数据记录")
    if duplicate_count:
        release_blockers.append(f"存在{duplicate_count}条重复记录")
    if silent_critical_risks:
        release_blockers.append(
            f"存在{len(silent_critical_risks)}个未进入review的关键字段风险"
        )
    return {
        "profile": profile_name,
        "record_count": len(records),
        "data_count": data_count,
        "review_count": review_count,
        "audit_count": audit_count,
        "warning_count": sum(bool(record.warnings) for record in records),
        "low_confidence_record_count": low_confidence_count,
        "duplicate_count": duplicate_count,
        "issue_types": dict(
            Counter(_issue_kind(issue) for record in records for issue in record.issues)
        ),
        "warning_types": dict(
            Counter(_issue_kind(warning) for record in records for warning in record.warnings)
        ),
        "silent_critical_risk_count": len(silent_critical_risks),
        "silent_critical_risk_examples": silent_critical_risks[:10],
        "structurally_valid": structurally_valid,
        "release_safe": not release_blockers,
        "release_blockers": release_blockers,
        # Backwards-compatible: `valid` has always meant structural validity only.
        "valid": structurally_valid,
    }
