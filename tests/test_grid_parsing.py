import re

from PIL import Image

from admission_pipeline import grid_parsing
from admission_pipeline.grid_parsing import (
    _GridCellOcrCache,
    _find_grid_rows,
    _reconcile_cell_code,
    _reconcile_repeated_values,
    _select_consensus_candidate,
    _semantic_reuse_key,
    _separate_major_code_from_name,
)
from admission_pipeline.domain import ExtractedPage, ParsedRecord, Token


def _token(text: str, x: float, y: float, score: float = 0.99) -> Token:
    return Token(text=text, score=score, x1=x - 2, y1=y - 2, x2=x + 2, y2=y + 2)


def test_repeated_major_code_is_removed_from_major_name() -> None:
    assert _separate_major_code_from_name(
        "007", "007秘书学", re.compile(r"[0-9A-Z]{3}")
    ) == ("007", "秘书学", "专业名称开头重复的专业代码已按列边界去除")


def test_missing_major_code_can_be_recovered_from_adjacent_cell() -> None:
    assert _separate_major_code_from_name(
        "", "003审计学", re.compile(r"[0-9A-Z]{3}")
    ) == ("003", "审计学", "专业代码从相邻专业名称单元格边界恢复")


def test_cell_ocr_requires_two_preprocessing_variants_to_agree() -> None:
    assert _select_consensus_candidate(
        [("008", 0.9998), ("008", 0.9995), ("800", 0.9999)]
    ) == ("008", 0.9995)
    assert _select_consensus_candidate([("008", 0.9998)]) == ("", 0.0)


def test_grid_cell_consensus_corrects_watermark_code_confusions() -> None:
    group_value, group_issues, group_warnings = _reconcile_cell_code(
        field="专业组代码",
        current="666",
        recognized="999",
        score=0.996,
        minimum_score=0.88,
        always_recheck=True,
    )
    major_value, major_issues, major_warnings = _reconcile_cell_code(
        field="专业代码",
        current="800",
        recognized="008",
        score=0.999,
        minimum_score=0.88,
        always_recheck=True,
    )
    assert group_value == "999"
    assert major_value == "008"
    assert not group_issues and not major_issues
    assert "666→999" in group_warnings[0]
    assert "800→008" in major_warnings[0]


def test_unconfirmed_always_rechecked_code_stays_in_review() -> None:
    value, issues, warnings = _reconcile_cell_code(
        field="专业代码",
        current="800",
        recognized="",
        score=0.0,
        minimum_score=0.88,
        always_recheck=True,
    )
    assert value == "800"
    assert issues == ["专业代码网格单元格双路复识未达可信阈值"]
    assert not warnings


def test_matching_cell_consensus_confirms_high_confidence_page_code() -> None:
    value, issues, warnings = _reconcile_cell_code(
        field="专业代码",
        current="010",
        current_score=0.999,
        recognized="010",
        score=0.48,
        minimum_score=0.88,
        always_recheck=True,
    )
    assert value == "010"
    assert not issues
    assert not warnings


def test_high_confidence_page_code_survives_when_cell_ocr_has_no_candidate() -> None:
    value, issues, warnings = _reconcile_cell_code(
        field="专业组代码",
        current="999",
        current_score=0.999,
        recognized="",
        score=0.0,
        minimum_score=0.88,
        always_recheck=True,
    )
    assert value == "999"
    assert not issues
    assert not warnings


def test_unconfirmed_group_code_uses_only_verified_strong_majority() -> None:
    records = [
        ParsedRecord(values={
            "科类": "历史类",
            "院校代码": f"100{index}",
            "专业组名称": "不限选考科目",
            "专业组代码": "999",
            "_grid_verified_fields": ["专业组代码"],
        })
        for index in range(3)
    ]
    records.append(ParsedRecord(
        values={
            "科类": "历史类",
            "院校代码": "1095",
            "专业组名称": "不限选考科目",
            "专业组代码": "666",
        },
        issues=["专业组代码网格单元格双路复识未达可信阈值"],
    ))
    records.append(ParsedRecord(values={
        "科类": "历史类",
        "院校代码": "1096",
        "专业组名称": "不限选考科目",
        "专业组代码": "666",
    }))

    _reconcile_repeated_values(records)

    assert records[-2].values["专业组代码"] == "999"
    assert not records[-2].issues
    assert "666→999" in records[-2].warnings[0]
    assert records[-1].values["专业组代码"] == "999"
    assert "666→999" in records[-1].warnings[0]


def test_two_variant_consensus_can_use_a_narrower_correction_threshold() -> None:
    value, issues, warnings = _reconcile_cell_code(
        field="专业代码",
        current="600",
        current_score=0.999,
        recognized="009",
        score=0.86,
        minimum_score=0.85,
        current_minimum_score=0.88,
        always_recheck=True,
    )
    assert value == "009"
    assert not issues
    assert "600→009" in warnings[0]


def test_grid_lines_recover_row_when_institution_code_anchor_is_invalid() -> None:
    page = ExtractedPage(
        source_relative_path="fujian/sample.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=100,
        height=100,
        tokens=[
            _token("1096", 25, 30),
            _token("福州理工学院", 35, 30),
            _token("500", 45, 30),
            _token("021", 65, 30),
            _token("电子信息工程", 75, 30),
            _token("267", 85, 30),
            _token("106", 25, 50, 0.73),
            _token("福州理工学院", 35, 50),
            _token("500", 45, 50),
            _token("022", 65, 50),
            _token("电子科学与技术", 75, 50),
            _token("47", 85, 50),
            _token("第3页，共37页", 65, 70),
        ],
    )
    rows = _find_grid_rows(
        page,
        vertical=[float(value) for value in range(0, 110, 10)],
        horizontal=[20.0, 40.0, 60.0, 80.0],
        anchor_column=2,
        anchor_pattern=re.compile(r"\d{4}"),
        field_columns={
            "院校代码": (2, 3),
            "院校名称": (3, 4),
            "专业组代码": (4, 5),
            "专业代码": (6, 7),
            "专业名称及专业方向": (7, 8),
            "计划数": (8, 9),
        },
        recover_unanchored=True,
        row_presence_fields=["专业代码", "专业名称及专业方向", "计划数"],
        row_presence_min_fields=2,
        row_header_terms=["专业代码", "专业名称", "计划数"],
    )

    assert len(rows) == 2
    assert not rows[0].recovered
    assert rows[1].recovered
    assert rows[1].anchor is None


def test_grid_lines_keep_empty_interior_band_for_review_instead_of_omitting() -> None:
    page = ExtractedPage(
        source_relative_path="scan/sample.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=100,
        height=100,
        tokens=[_token("1096", 25, 30), _token("1097", 25, 70)],
    )

    rows = _find_grid_rows(
        page,
        vertical=[float(value) for value in range(0, 110, 10)],
        horizontal=[20.0, 40.0, 60.0, 80.0],
        anchor_column=2,
        anchor_pattern=re.compile(r"\d{4}"),
        field_columns={
            "专业代码": (6, 7),
            "专业名称及专业方向": (7, 8),
            "计划数": (8, 9),
        },
        recover_unanchored=True,
        row_presence_fields=["专业代码", "专业名称及专业方向", "计划数"],
        row_presence_min_fields=1,
        row_header_terms=["专业代码", "专业名称", "计划数"],
    )

    assert len(rows) == 3
    assert rows[1].recovered
    assert rows[1].presence_count == 0


def test_same_name_strong_majority_repairs_adjacent_swap_and_truncated_code() -> None:
    records = [
        ParsedRecord(values={
            "来源文件": "fujian/physics.pdf",
            "科类": "物理类",
            "院校代码": "1096",
            "院校名称": "福州理工学院",
            "专业组代码": "500",
        })
        for _ in range(4)
    ]
    records.extend([
        ParsedRecord(
            values={
                "来源文件": "fujian/physics.pdf",
                "科类": "物理类",
                "院校代码": "1906",
                "院校名称": "福州理工学院",
                "专业组代码": "500",
            },
            issues=["院校代码网格单元格双路复识未达可信阈值"],
        ),
        ParsedRecord(
            values={
                "来源文件": "fujian/physics.pdf",
                "科类": "物理类",
                "院校代码": "106",
                "院校名称": "福州理工学院",
                "专业组代码": "500",
            },
            issues=["院校代码格式异常（106）"],
        ),
    ])

    _reconcile_repeated_values(records)

    assert {record.values["院校代码"] for record in records} == {"1096"}
    assert not records[-2].issues
    assert not records[-1].issues
    assert "1906→1096" in records[-2].warnings[0]
    assert "106→1096" in records[-1].warnings[0]


def test_grid_cell_ocr_cache_avoids_repeating_recognition(tmp_path, monkeypatch) -> None:
    calls = 0

    def fake_recognize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "008", 0.998

    monkeypatch.setattr(grid_parsing, "_recognize_cell", fake_recognize)
    image = Image.new("RGB", (100, 100), "white")
    arguments = {
        "page_number": 1,
        "field": "专业代码",
        "image": image,
        "x1": 10,
        "x2": 30,
        "y1": 20,
        "y2": 40,
        "ocr": object(),
        "pattern": re.compile(r"\d{3}"),
    }
    first = _GridCellOcrCache(
        tmp_path, source_sha256="source", profile_name="grid"
    )
    assert first.recognize(**arguments) == ("008", 0.998)
    assert first.misses == 1
    first.save()

    second = _GridCellOcrCache(
        tmp_path, source_sha256="source", profile_name="grid"
    )
    assert second.recognize(**arguments) == ("008", 0.998)
    assert second.hits == 1
    assert calls == 1


def test_semantic_cell_reuse_is_scoped_by_source_course_and_label() -> None:
    base = {
        "来源文件": "fujian/history.pdf",
        "科类": "历史类",
        "专业组名称": "不限选考科目",
    }
    key = _semantic_reuse_key("专业组代码", base)

    assert key != _semantic_reuse_key(
        "专业组代码", {**base, "科类": "物理类"}
    )
    assert key != _semantic_reuse_key(
        "专业组代码", {**base, "专业组名称": "选考化学"}
    )
    assert key != _semantic_reuse_key(
        "专业组代码", {**base, "来源文件": "fujian/physics.pdf"}
    )
