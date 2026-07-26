from admission_pipeline.domain import ParsedRecord
from admission_pipeline.refinement import _approve_consistent_plan_refinements


def test_refined_plan_is_promoted_only_against_original_school_total() -> None:
    record = ParsedRecord(
        values={
            "来源文件": "demo.pdf",
            "院校代码": "0001",
            "院校计划总数": "1",
            "计划数": "",
            "_ocr_candidates": {"计划数": "1"},
        },
        issues=["缺少必填字段：计划数"],
    )
    _approve_consistent_plan_refinements([record])
    assert record.values["计划数"] == "1"
    assert not record.issues


def test_two_refined_totals_do_not_validate_each_other() -> None:
    record = ParsedRecord(
        values={
            "来源文件": "demo.pdf",
            "院校代码": "0001",
            "院校计划总数": "",
            "计划数": "",
            "_ocr_candidates": {"院校计划总数": "5", "计划数": "5"},
        },
        issues=["缺少必填字段：院校计划总数", "缺少必填字段：计划数"],
    )
    _approve_consistent_plan_refinements([record])
    assert record.values["院校计划总数"] == ""
    assert record.values["计划数"] == ""
    assert len(record.issues) == 2
