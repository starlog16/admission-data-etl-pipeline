import re
from types import SimpleNamespace

from PIL import Image

from admission_pipeline.domain import ExtractedPage, ParsedRecord
from admission_pipeline.refinement import (
    _approve_consistent_plan_refinements,
    _is_consensus_arithmetic_candidate,
    _parse_numeric_pair,
    _recognize_cell,
    apply_verified_record_corrections,
)


def test_low_score_consensus_is_reserved_for_arithmetic_fields() -> None:
    pattern = re.compile(r"\d{1,3}")

    assert _is_consensus_arithmetic_candidate("计划数", "54", pattern, 3)
    assert not _is_consensus_arithmetic_candidate("计划数", "54", pattern, 1)
    assert not _is_consensus_arithmetic_candidate("学制", "4", pattern, 3)


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


def test_group_total_candidate_is_promoted_against_original_major_plans() -> None:
    records = [
        ParsedRecord(
            values={
                "来源文件": "demo.pdf", "院校代码": "0001",
                "院校专业组代码": "0001101", "专业组计划数": "",
                "计划数": "2", "_ocr_candidates": {"专业组计划数": "3"},
            },
            issues=["缺少必填字段：专业组计划数"],
        ),
        ParsedRecord(
            values={
                "来源文件": "demo.pdf", "院校代码": "0001",
                "院校专业组代码": "0001101", "专业组计划数": "",
                "计划数": "1", "_ocr_candidates": {"专业组计划数": "3"},
            },
            issues=["缺少必填字段：专业组计划数"],
        ),
    ]
    _approve_consistent_plan_refinements(records)
    assert all(record.values["专业组计划数"] == "3" for record in records)
    assert all(not record.issues for record in records)


def test_existing_bad_major_plan_is_promoted_only_when_group_sum_matches() -> None:
    records = [
        ParsedRecord(
            values={
                "来源文件": "demo.pdf", "科类": "物理类", "再选科目": "",
                "院校代码": "6656", "院校专业组代码": "6656101",
                "专业组计划数": "10", "计划数": "888",
                "_ocr_candidates": {"计划数": "8"},
            },
            issues=["专业计划数合计 890 与专业组计划数 10 不一致"],
        ),
        ParsedRecord(
            values={
                "来源文件": "demo.pdf", "科类": "物理类", "再选科目": "",
                "院校代码": "6656", "院校专业组代码": "6656101",
                "专业组计划数": "10", "计划数": "2",
            },
            issues=["专业计划数合计 890 与专业组计划数 10 不一致"],
        ),
    ]

    _approve_consistent_plan_refinements(records)

    assert records[0].values["计划数"] == "8"
    assert records[0].values["OCR补识别字段"] == "计划数"
    assert records[1].values["计划数"] == "2"


def test_school_total_crosscheck_includes_different_subject_requirements() -> None:
    records = [
        ParsedRecord(values={
            "来源文件": "demo.pdf", "院校代码": "0001", "再选科目": "不限",
            "院校计划总数": "3", "计划数": "14",
            "_ocr_candidates": {"计划数": "1"},
        }),
        ParsedRecord(values={
            "来源文件": "demo.pdf", "院校代码": "0001", "再选科目": "化学",
            "院校计划总数": "3", "计划数": "2",
        }),
    ]

    _approve_consistent_plan_refinements(records)

    assert [record.values["计划数"] for record in records] == ["1", "2"]


def test_single_missing_plan_uses_only_unique_positive_residual() -> None:
    records = [
        ParsedRecord(values={
            "来源文件": "demo.pdf", "院校代码": "0001",
            "院校计划总数": "8", "计划数": "3",
        }),
        ParsedRecord(
            values={
                "来源文件": "demo.pdf", "院校代码": "0001",
                "院校计划总数": "8", "计划数": "",
            },
            issues=["缺少必填字段：计划数"],
        ),
    ]

    _approve_consistent_plan_refinements(records)

    assert records[1].values["计划数"] == "5"
    assert records[1].quality_bucket == "audit"
    assert records[1].values["_field_evidence"]["计划数"]["status"] == (
        "arithmetic_residual_recovered"
    )


def test_verified_record_correction_requires_exactly_one_match() -> None:
    record = ParsedRecord(
        values={
            "来源文件": "henan/2026/demo.pdf",
            "来源页": "35",
            "_major_side": "right",
            "院校代码": "6055",
            "专业组代码": "901",
            "专业代码": "70",
            "专业名称及专业方向": "软件工程(融媒体应用开发)",
        },
        issues=[],
    )
    profile = {
        "verified_record_corrections": [{
            "match": {
                "来源页": "35", "_major_side": "right",
                "院校代码": "6055", "专业组代码": "901", "专业代码": "70",
            },
            "contains": {"专业名称及专业方向": "融媒体应用开发"},
            "set": {
                "院校代码": "6060", "院校名称": "周口师范学院",
                "院校专业组代码": "6060902", "专业组代码": "902",
            },
        }]
    }

    apply_verified_record_corrections([record], profile)

    assert record.values["院校代码"] == "6060"
    assert record.values["专业组代码"] == "902"


def test_verified_record_correction_skips_other_source_and_clears_declared_issue() -> None:
    history = ParsedRecord(
        values={"来源文件": "历史科目组.pdf", "院校代码": "3833", "计划数": ""},
        issues=["缺少必填字段：计划数"],
    )
    physics = ParsedRecord(
        values={"来源文件": "物理科目组.pdf", "院校代码": "9999", "计划数": "2"}
    )
    profile = {
        "verified_record_corrections": [{
            "scope_contains": {"来源文件": "历史科目组"},
            "match": {"院校代码": "3833"},
            "set": {"计划数": "1"},
            "remove_issues": ["缺少必填字段：计划数"],
            "note": "源PDF已核验",
        }]
    }
    apply_verified_record_corrections([history], profile)
    apply_verified_record_corrections([physics], profile)
    assert history.values["计划数"] == "1"
    assert not history.issues
    assert history.warnings == ["源PDF已核验"]
    assert physics.values["计划数"] == "2"


def test_numeric_pair_uses_last_three_digits_as_score() -> None:
    assert _parse_numeric_pair("8 389") == {"计划数": "8", "分数线": "389"}
    assert _parse_numeric_pair("1419") == {"计划数": "1", "分数线": "419"}
    assert _parse_numeric_pair("419") == {"分数线": "419"}
    assert _parse_numeric_pair("12000") == {}


def test_color_aware_cell_ocr_requires_two_variants_to_agree() -> None:
    answers = iter([("8", 0.99), ("8", 0.98), ("3", 0.999)])

    def fake_ocr(*args, **kwargs):
        text, score = next(answers)
        return SimpleNamespace(txts=[text], scores=[score])

    page = ExtractedPage(
        source_relative_path="demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=100,
        height=100,
        tokens=[],
    )

    result = _recognize_cell(
        Image.new("RGB", (100, 100), "white"),
        page,
        20,
        30,
        [0.3, 0.4],
        fake_ocr,
        scale=2,
    )

    assert result == {"text": "8", "score": 0.98, "votes": 2}
