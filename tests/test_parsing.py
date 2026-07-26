from admission_pipeline.domain import ExtractedPage, Token
from admission_pipeline.parsing import (
    _strip_cjk_noise_from_typed_value,
    parse_spatial_rows,
    parse_structured_table,
    parse_two_column_hierarchical_plan,
)


def test_spatial_parser_assigns_columns_and_flags_bad_integer() -> None:
    page = ExtractedPage(
        source_relative_path="anhui/2026/demo.jpg",
        source_sha256="abc",
        page_number=1,
        method="image_ocr",
        width=1000,
        height=1000,
        tokens=[
            Token("0012", 0.99, 10, 100, 80, 120),
            Token("测试大学", 0.99, 120, 100, 300, 120),
            Token("001专业组", 0.99, 400, 100, 600, 120),
            Token("六百", 0.99, 780, 100, 850, 120),
            Token("12345", 0.99, 900, 100, 980, 120),
        ],
    )
    profile = {
        "columns": [
            {"name": "院校代码", "x_min": 0.0, "x_max": 0.1, "type": "code"},
            {"name": "院校名称", "x_min": 0.1, "x_max": 0.36, "type": "text"},
            {"name": "专业组", "x_min": 0.36, "x_max": 0.7, "type": "text"},
            {"name": "最低分", "x_min": 0.7, "x_max": 0.88, "type": "integer"},
            {"name": "名次", "x_min": 0.88, "x_max": 1.0, "type": "integer"},
        ],
        "required": ["院校代码", "院校名称", "专业组", "最低分", "名次"],
        "minimum_populated_fields": 4,
    }
    records = parse_spatial_rows([page], profile, y_tolerance_ratio=0.65)
    assert records[0].values["院校代码"] == "0012"
    assert any("最低分" in issue for issue in records[0].issues)


def test_typed_values_strip_only_unambiguous_cjk_noise() -> None:
    assert _strip_cjk_noise_from_typed_value("4723安徽", "integer") == "4723"
    assert _strip_cjk_noise_from_typed_value("安徽9004", "code") == "9004"
    assert _strip_cjk_noise_from_typed_value("院55865", "integer") == "55865"
    assert _strip_cjk_noise_from_typed_value("L78", "integer") == "L78"
    assert _strip_cjk_noise_from_typed_value("001专业组", "text") == "001专业组"


def test_two_column_plan_parser_fills_school_and_merges_wrapped_text() -> None:
    page = ExtractedPage(
        source_relative_path="hebei/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=1000,
        height=1400,
        tokens=[
            Token("2026年河北省普通高校招生征集志愿计划", 0.99, 50, 20, 350, 40),
            Token("非定向/本科批/物理科目组合", 0.99, 520, 20, 850, 40),
            Token("0053 白城师范学院[公办]", 0.99, 60, 100, 250, 120),
            Token("3", 0.99, 390, 100, 400, 120),
            Token("35 学前教育(中外合作办学) 不限", 0.99, 70, 140, 330, 160),
            Token("3", 0.99, 390, 140, 400, 160),
            Token("4", 0.99, 420, 140, 430, 160),
            Token("26000", 0.99, 455, 140, 495, 160),
            Token("[不招色盲", 0.99, 90, 170, 240, 190),
            Token("色弱考生]", 0.99, 90, 200, 240, 220),
            Token("0248成都信息工程大学(中外高水平大学学生交流", 0.99, 520, 100, 790, 120),
            Token("1", 0.99, 850, 100, 860, 120),
            Token("计划)[公办]", 0.99, 550, 130, 680, 150),
            Token("02 环境科学", 0.99, 535, 170, 650, 190),
            Token("化学", 0.99, 750, 170, 780, 190),
            Token("1", 0.99, 850, 170, 860, 190),
            Token("4", 0.99, 880, 170, 890, 190),
            Token("6240", 0.99, 920, 170, 960, 190),
        ],
    )
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5],
                "description": [0.05, 0.275],
                "subject": [0.275, 0.374],
                "plan": [0.374, 0.413],
                "duration": [0.413, 0.44],
                "tuition": [0.44, 0.5],
            },
            "right": {
                "page": [0.5, 1.0],
                "description": [0.515, 0.742],
                "subject": [0.742, 0.83],
                "plan": [0.83, 0.868],
                "duration": [0.868, 0.895],
                "tuition": [0.895, 1.0],
            },
        },
        "school_pattern": r"^(\d{4})\s*(.+)$",
        "major_pattern": r"^([0-9A-Za-z]{2})\s*(.+)$",
        "subject_keywords": ["化学", "不限"],
        "required": ["院校代码", "院校名称", "院校计划总数", "专业代码", "专业名称及专业方向", "计划数"],
        "integer_fields": ["院校计划总数", "计划数", "学制"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"\d{2}"},
        "skip_if_contains": ["普通高校招生征集志愿计划"],
    }
    records = parse_two_column_hierarchical_plan(
        [page], profile, y_tolerance_ratio=0.65
    )
    assert len(records) == 2
    assert records[0].values["院校代码"] == "0053"
    assert records[0].values["院校性质"] == "公办"
    assert records[0].values["再选科目"] == "不限"
    assert records[0].values["备注"] == "[不招色盲色弱考生]"
    assert records[1].values["院校代码"] == "0248"
    assert records[1].values["院校名称"].endswith("学生交流计划)")
    assert records[1].values["专业代码"] == "02"
    assert records[0].values["_major_side"] == "left"
    assert records[1].values["_school_side"] == "right"


def test_structured_table_uses_cells_and_section_context() -> None:
    page = ExtractedPage(
        source_relative_path="xinjiang/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_text",
        width=100,
        height=100,
        tables=[[
            ["院校代号", "院校名称", "计划数", "投档人数", "最高分", "最低分"],
            ["国家及地方专项", "", "", "", "", ""],
            ["5001", "北京大学", "3", "3", "629", "623"],
        ]],
    )
    profile = {
        "constants": {"数据类型": "投档数据"},
        "column_map": {
            "院校代码": 0, "院校名称": 1, "计划数": 2,
            "投档人数": 3, "最高分": 4, "最低分": 5,
        },
        "row_key": {"field": "院校代码", "pattern": r"\d{4}"},
        "section_pattern": r"国家及地方专项",
        "section_field": "计划类别",
        "required": ["院校代码", "院校名称", "最低分"],
        "integer_fields": ["计划数", "投档人数", "最高分", "最低分"],
    }
    records = parse_structured_table([page], profile, y_tolerance_ratio=0.65)
    assert len(records) == 1
    assert records[0].values["计划类别"] == "国家及地方专项"
    assert records[0].values["省份"] == "新疆"
    assert not records[0].issues
