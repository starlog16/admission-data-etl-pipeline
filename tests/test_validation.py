import pytest

from admission_pipeline.domain import ParseDiagnostics, ParsedRecord
from admission_pipeline.validation import validate_records


def test_unresolved_critical_evidence_without_review_blocks_release() -> None:
    record = ParsedRecord(
        values={
            "院校代码": "1906",
            "_field_evidence": {
                "院校代码": {
                    "raw_value": "1906",
                    "final_value": "1906",
                    "status": "unresolved",
                }
            },
        }
    )

    summary = validate_records([record], "demo")

    assert summary["silent_critical_risk_count"] == 1
    assert not summary["release_safe"]


def test_unresolved_critical_evidence_is_safe_from_silent_leak_when_reviewed() -> None:
    record = ParsedRecord(
        values={
            "院校代码": "1906",
            "_field_evidence": {
                "院校代码": {
                    "raw_value": "1906",
                    "final_value": "1906",
                    "status": "unresolved",
                }
            },
        },
        issues=["院校代码整页OCR与单元格OCR不一致"],
    )

    summary = validate_records([record], "demo")

    assert summary["silent_critical_risk_count"] == 0
    assert summary["release_safe"]
    assert summary["review_count"] == 1
    assert summary["data_count"] == 0


def test_observable_parse_diagnostics_stop_when_a_source_unit_is_not_emitted() -> None:
    diagnostics = ParseDiagnostics(
        parser="ruled_grid_table",
        coverage_observable=True,
        source_units_detected=10,
        records_emitted=9,
        unparsed_units=1,
    )

    with pytest.raises(RuntimeError, match="解析完整性保护触发"):
        diagnostics.assert_complete()
