from admission_pipeline.domain import ExtractedPage, ParseDiagnostics, ParsedRecord, Token
from admission_pipeline.parsing import (
    _extract_document_context,
    _strip_cjk_noise_from_integer_or_allowed,
    _strip_cjk_noise_from_typed_value,
    _tokens_in_x_band,
    parse_spatial_rows,
    parse_structured_table,
    parse_two_column_hierarchical_plan,
    parse_two_column_plan_balance_score,
    recheck_plan_totals,
)


def test_document_context_maps_again_recruitment_to_second_round() -> None:
    page = ExtractedPage(
        source_relative_path="henan/2026/普通本科批再次征集志愿高校分专业名单.pdf",
        source_sha256="abc",
        page_number=1,
        method="image_ocr",
        width=100,
        height=100,
        tokens=[],
    )

    context = _extract_document_context([page], {})

    assert context["省份"] == "河南"
    assert context["年份"] == "2026"
    assert context["征集轮次"] == "2"


def test_document_context_keeps_path_province_over_school_location() -> None:
    page = ExtractedPage(
        source_relative_path="qinghai/2026/征集志愿/计划.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_text",
        width=100,
        height=100,
        tokens=[Token("2026年青海省征集志愿计划，办学地点：广西壮族自治区北海市", 1.0, 0, 0, 100, 10)],
    )

    context = _extract_document_context([page], {})

    assert context["省份"] == "青海"


def test_integer_or_allowed_cleanup_removes_watermark_noise_conservatively() -> None:
    allowed = {"待定", "免费"}
    noise = {"考", "试", "院"}
    assert _strip_cjk_noise_from_integer_or_allowed("试27800", allowed, noise) == "27800"
    assert _strip_cjk_noise_from_integer_or_allowed("考待定", allowed, noise) == "待定"
    assert _strip_cjk_noise_from_integer_or_allowed("免费", allowed, noise) == "免费"
    assert _strip_cjk_noise_from_integer_or_allowed("约20000", allowed, noise) == "约20000"


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


def test_column_ownership_uses_token_center_not_left_edge() -> None:
    tuition = Token("100000", 1.0, 438.5, 100, 481.9, 120)

    assert not _tokens_in_x_band([tuition], 1000, [0.413, 0.44])
    assert _tokens_in_x_band([tuition], 1000, [0.44, 0.50]) == [tuition]


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


def test_two_column_parser_follows_page_then_column_reading_order() -> None:
    pages = [
        ExtractedPage(
            source_relative_path="hebei/2026/demo.pdf",
            source_sha256="abc",
            page_number=1,
            method="pdf_page_ocr",
            width=1000,
            height=1400,
            tokens=[
                Token("0001 跨栏大学[公办]", 0.99, 60, 100, 250, 120),
                Token("2", 0.99, 390, 100, 400, 120),
                Token("01 汉语言文学", 0.99, 535, 100, 700, 120),
                Token("不限", 0.99, 750, 100, 790, 120),
                Token("1", 0.99, 850, 100, 860, 120),
                Token("4", 0.99, 880, 100, 890, 120),
                Token("6000", 0.99, 920, 100, 960, 120),
            ],
        ),
        ExtractedPage(
            source_relative_path="hebei/2026/demo.pdf",
            source_sha256="abc",
            page_number=2,
            method="pdf_page_ocr",
            width=1000,
            height=1400,
            tokens=[
                Token("02 数学与应用数学", 0.99, 70, 100, 250, 120),
                Token("不限", 0.99, 290, 100, 330, 120),
                Token("1", 0.99, 390, 100, 400, 120),
                Token("4", 0.99, 420, 100, 430, 120),
                Token("5000", 0.99, 455, 100, 490, 120),
                Token("0002 新大学[公办]", 0.99, 535, 100, 700, 120),
                Token("1", 0.99, 850, 100, 860, 120),
                Token("03 物理学", 0.99, 535, 140, 700, 160),
                Token("不限", 0.99, 750, 100, 790, 120),
                Token("1", 0.99, 850, 140, 860, 160),
                Token("4", 0.99, 880, 140, 890, 160),
                Token("5000", 0.99, 920, 140, 960, 160),
            ],
        ),
    ]
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5], "description": [0.05, 0.275],
                "subject": [0.275, 0.374], "plan": [0.374, 0.413],
                "duration": [0.413, 0.44], "tuition": [0.44, 0.5],
            },
            "right": {
                "page": [0.5, 1.0], "description": [0.515, 0.742],
                "subject": [0.742, 0.83], "plan": [0.83, 0.868],
                "duration": [0.868, 0.895], "tuition": [0.895, 1.0],
            },
        },
        "school_pattern": r"^(\d{4})\s*(.+)$",
        "major_pattern": r"^([0-9A-Za-z]{2})\s*([\u3400-\u9fff（(].+)$",
        "subject_keywords": ["不限"],
        "required": ["院校代码", "院校名称", "院校计划总数", "专业代码", "计划数"],
        "integer_fields": ["院校计划总数", "计划数", "学制"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"\d{2}"},
    }
    diagnostics = ParseDiagnostics(parser="two_column_hierarchical_plan")

    records = parse_two_column_hierarchical_plan(
        pages, profile, y_tolerance_ratio=0.65, diagnostics=diagnostics
    )

    assert [(item.values["院校代码"], item.values["专业代码"]) for item in records] == [
        ("0001", "01"), ("0001", "02"), ("0002", "03")
    ]
    assert diagnostics.coverage_observable
    assert diagnostics.source_units_detected == diagnostics.records_emitted == 3
    diagnostics.assert_complete()


def test_two_column_parser_retains_major_with_damaged_code_anchor() -> None:
    page = ExtractedPage(
        source_relative_path="hebei/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=1000,
        height=1400,
        tokens=[
            Token("0001 测试大学[公办]", 0.99, 60, 100, 250, 120),
            Token("1", 0.99, 390, 100, 400, 120),
            Token("001 环境设计", 0.70, 70, 140, 250, 160),
            Token("不限", 0.99, 290, 140, 330, 160),
            Token("1", 0.99, 390, 140, 400, 160),
            Token("4", 0.99, 420, 140, 430, 160),
            Token("5000", 0.99, 455, 140, 490, 160),
            Token("第", 0.99, 420, 1340, 430, 1360),
            Token("1", 0.99, 390, 1340, 400, 1360),
        ],
    )
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5], "description": [0.05, 0.275],
                "subject": [0.275, 0.374], "plan": [0.374, 0.413],
                "duration": [0.413, 0.44], "tuition": [0.44, 0.5],
            },
            "right": {
                "page": [0.5, 1.0], "description": [0.515, 0.742],
                "subject": [0.742, 0.83], "plan": [0.83, 0.868],
                "duration": [0.868, 0.895], "tuition": [0.895, 1.0],
            },
        },
        "school_pattern": r"^(\d{4})\s*(.+)$",
        "major_pattern": r"^([0-9A-Za-z]{2})\s*([\u3400-\u9fff（(].+)$",
        "subject_keywords": ["不限"],
        "required": ["院校代码", "专业代码", "计划数"],
        "integer_fields": ["院校计划总数", "计划数", "学制"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"\d{2}"},
    }
    diagnostics = ParseDiagnostics(parser="two_column_hierarchical_plan")

    records = parse_two_column_hierarchical_plan(
        [page], profile, y_tolerance_ratio=0.65, diagnostics=diagnostics
    )

    assert len(records) == 1
    assert records[0].values["专业代码"] == "001"
    assert records[0].needs_review
    assert any("专业代码锚点异常" in issue for issue in records[0].issues)
    assert diagnostics.recovered_units == 1


def test_two_column_parser_retains_numeric_only_major_candidate() -> None:
    page = ExtractedPage(
        source_relative_path="hebei/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=1000,
        height=1400,
        tokens=[
            Token("0001 测试大学[公办]", 0.99, 60, 100, 250, 120),
            Token("1", 0.99, 390, 100, 400, 120),
            Token("不限", 0.99, 290, 140, 330, 160),
            Token("1", 0.99, 390, 140, 400, 160),
            Token("4", 0.99, 420, 140, 430, 160),
            Token("5000", 0.99, 455, 140, 490, 160),
        ],
    )
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5], "description": [0.05, 0.275],
                "subject": [0.275, 0.374], "plan": [0.374, 0.413],
                "duration": [0.413, 0.44], "tuition": [0.44, 0.5],
            },
            "right": {
                "page": [0.5, 1.0], "description": [0.515, 0.742],
                "subject": [0.742, 0.83], "plan": [0.83, 0.868],
                "duration": [0.868, 0.895], "tuition": [0.895, 1.0],
            },
        },
        "school_pattern": r"^(\d{4})\s*(.+)$",
        "major_pattern": r"^([0-9A-Za-z]{2})\s*([\u3400-\u9fff（(].+)$",
        "subject_keywords": ["不限"],
        "required": ["院校代码", "专业代码", "专业名称及专业方向", "计划数"],
        "integer_fields": ["院校计划总数", "计划数", "学制"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"\d{2}"},
    }
    diagnostics = ParseDiagnostics(parser="two_column_hierarchical_plan")

    records = parse_two_column_hierarchical_plan(
        [page], profile, y_tolerance_ratio=0.65, diagnostics=diagnostics
    )

    assert len(records) == 1
    assert records[0].values["专业代码"] == ""
    assert records[0].values["计划数"] == "1"
    assert records[0].needs_review
    assert diagnostics.source_units_detected == diagnostics.records_emitted == 1
    assert diagnostics.recovered_units == 1


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


def test_structured_table_validates_configured_code_patterns() -> None:
    page = ExtractedPage(
        source_relative_path="qinghai/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_text",
        width=100,
        height=100,
        tables=[[['2161', '测试学院', '30', '环境设计', '2']]],
    )
    profile = {
        "column_map": {
            "院校代码": 0,
            "院校名称": 1,
            "专业代码": 2,
            "专业名称及专业方向": 3,
            "计划数": 4,
        },
        "row_key": {"field": "院校代码", "pattern": r"\d{4}"},
        "required": ["院校代码", "院校名称", "专业代码", "计划数"],
        "integer_fields": ["计划数"],
        "code_fields": ["院校代码", "专业代码"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"[0-9A-Z]{3}"},
    }

    records = parse_structured_table([page], profile, y_tolerance_ratio=0.65)

    assert len(records) == 1
    assert records[0].issues == ["专业代码：代码格式异常（30）"]


def test_structured_table_removes_only_configured_leading_watermark_line() -> None:
    page = ExtractedPage(
        source_relative_path="qinghai/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_text",
        width=100,
        height=100,
        tables=[[['青\n本科普通类', '2161', '教\n测试学院', '303', '育\n环境设计', '试\n27800', '院\n3', '院\n办学地点:测试市']]],
    )
    profile = {
        "column_map": {
            "计划类别": 0, "院校代码": 1, "院校名称": 2,
            "专业代码": 3, "专业名称及专业方向": 4,
            "学费": 5, "计划数": 6, "备注": 7,
        },
        "row_key": {"field": "院校代码", "pattern": r"\d{4}"},
        "required": ["院校代码", "院校名称", "专业代码", "计划数"],
        "integer_fields": ["计划数", "学费"],
        "code_fields": ["院校代码", "专业代码"],
        "watermark_noise_chars": ["青", "教", "育", "试", "院"],
        "watermark_line_fields": ["计划类别", "院校名称", "专业名称及专业方向", "学费", "计划数", "备注"],
    }

    records = parse_structured_table([page], profile, y_tolerance_ratio=0.65)

    assert len(records) == 1
    assert records[0].values["计划类别"] == "本科普通类"
    assert records[0].values["院校名称"] == "测试学院"
    assert records[0].values["专业名称及专业方向"] == "环境设计"
    assert records[0].values["学费"] == "27800"
    assert records[0].values["计划数"] == "3"
    assert records[0].values["备注"] == "办学地点:测试市"
    assert not records[0].issues


def test_structured_table_maps_dynamic_headers_and_audits_zero_admission() -> None:
    page = ExtractedPage(
        source_relative_path="guizhou/2026/第2次征集志愿投档情况（首选科目物理）.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_text",
        width=100,
        height=100,
        tables=[[
            ["序号", "院校代号", "院校名称", "专业代号", "专业名称", "投档数", "投档最低分", "投档最低位次"],
            ["1", "0012", "测试大学", "03", "计算机科学与技术", "1", "588", "12345"],
            ["2", "0013", "示例学院", "04", "数学与应用数学", "0", "", ""],
        ]],
    )
    profile = {
        "constants": {"数据类型": "投档数据"},
        "dynamic_header_mapping": True,
        "_header_aliases": {
            "序号": ["序号"], "院校代码": ["院校代码", "院校代号"],
            "院校名称": ["院校名称"], "专业代码": ["专业代码", "专业代号"],
            "专业名称及专业方向": ["专业名称"], "投档人数": ["投档数"],
            "最低分": ["投档最低分"], "最低分名次": ["投档最低位次"],
        },
        "header_fields": ["序号", "院校代码", "院校名称", "专业代码", "专业名称及专业方向", "投档人数", "最低分", "最低分名次"],
        "required_header_fields": ["序号", "院校代码", "专业代码", "投档人数", "最低分", "最低分名次"],
        "row_key": {"field": "序号", "pattern": r"\d+"},
        "required": ["院校代码", "院校名称", "专业代码", "专业名称及专业方向", "投档人数"],
        "integer_fields": ["序号", "投档人数", "最低分", "最低分名次"],
        "code_fields": ["院校代码", "专业代码"],
        "conditional_required": [{"field": "投档人数", "pattern": r"[1-9]\d*", "required": ["最低分", "最低分名次"]}],
        "conditional_warnings": [{"field": "投档人数", "pattern": "0", "message": "零投档"}],
    }
    records = parse_structured_table([page], profile, y_tolerance_ratio=0.65)
    assert len(records) == 2
    assert records[0].values["院校代码"] == "0012"
    assert records[0].values["最低分名次"] == "12345"
    assert records[0].values["科类"] == "物理类"
    assert records[0].values["征集轮次"] == "2"
    assert not records[0].issues
    assert not records[1].issues
    assert records[1].warnings == ["零投档"]


def test_structured_table_safely_splits_multiple_major_lines() -> None:
    page = ExtractedPage(
        source_relative_path="demo.xlsx", source_sha256="abc", page_number=1,
        method="spreadsheet", width=100, height=100,
        tables=[[
            ["序号", "院校代码", "专业代码", "专业名称", "计划数"],
            ["1", "0012", "01\n02", "汉语言文学\n历史学", "3\n2"],
        ]],
    )
    profile = {
        "dynamic_header_mapping": True,
        "_header_aliases": {
            "序号": ["序号"], "院校代码": ["院校代码"],
            "专业代码": ["专业代码"], "专业名称及专业方向": ["专业名称"],
            "计划数": ["计划数"],
        },
        "header_fields": ["序号", "院校代码", "专业代码", "专业名称及专业方向", "计划数"],
        "required_header_fields": ["序号", "院校代码", "专业代码", "专业名称及专业方向", "计划数"],
        "row_key": {"field": "序号", "pattern": r"\d+"},
        "required": ["院校代码", "专业代码", "专业名称及专业方向", "计划数"],
        "integer_fields": ["序号", "计划数"],
        "code_fields": ["院校代码", "专业代码"],
        "split_merged_rows": {
            "anchor_field": "专业代码", "anchor_pattern": r"\d{2}",
            "fields": ["专业代码", "专业名称及专业方向", "计划数"],
        },
    }
    records = parse_structured_table([page], profile, y_tolerance_ratio=0.65)
    assert [(item.values["专业代码"], item.values["专业名称及专业方向"], item.values["计划数"]) for item in records] == [
        ("01", "汉语言文学", "3"), ("02", "历史学", "2")
    ]
    assert all(item.warnings for item in records)


def test_structured_table_retains_data_row_with_damaged_row_key() -> None:
    page = ExtractedPage(
        source_relative_path="demo.xlsx",
        source_sha256="abc",
        page_number=1,
        method="spreadsheet",
        width=100,
        height=100,
        tables=[[{
            "序号": "序号",
            "院校代码": "院校代码",
            "专业代码": "专业代码",
            "计划数": "计划数",
        }.values(), ["I", "0012", "03", "2"]]],
    )
    profile = {
        "column_map": {"序号": 0, "院校代码": 1, "专业代码": 2, "计划数": 3},
        "row_key": {"field": "序号", "pattern": r"\d+"},
        "required": ["序号", "院校代码", "专业代码", "计划数"],
        "integer_fields": ["序号", "计划数"],
        "code_patterns": {"院校代码": r"\d{4}", "专业代码": r"\d{2}"},
        "skip_if_contains": ["院校代码|专业代码"],
    }
    diagnostics = ParseDiagnostics(parser="structured_table")

    records = parse_structured_table(
        [page], profile, y_tolerance_ratio=0.65, diagnostics=diagnostics
    )

    assert len(records) == 1
    assert records[0].values["院校代码"] == "0012"
    assert any("行关键字段异常" in issue for issue in records[0].issues)
    assert diagnostics.source_units_detected == 1
    assert diagnostics.records_emitted == 1
    assert diagnostics.recovered_units == 1
    diagnostics.assert_complete()


def test_two_column_plan_balance_score_parses_group_major_and_context() -> None:
    page = ExtractedPage(
        source_relative_path="henan/2026/demo.pdf",
        source_sha256="abc",
        page_number=2,
        method="pdf_page_ocr",
        width=1000,
        height=1400,
        tokens=[
            Token("历史类", 0.99, 180, 50, 260, 75),
            Token("再选：不限", 0.99, 360, 80, 450, 105),
            Token("1435 北京语言大学 第G15组(高校专项计划组)", 0.99, 55, 120, 350, 145),
            Token("1", 0.99, 430, 120, 445, 145),
            Token("19 外国语言文学类(英语和翻译)(新增计划；学费：6000元/年)", 0.99, 65, 160, 410, 185),
            Token("1 594", 0.99, 430, 160, 490, 185),
            Token("物理类", 0.99, 650, 50, 730, 75),
            Token("再选：生物", 0.99, 820, 80, 900, 105),
            Token("2440 长江大学 第111", 0.99, 515, 120, 790, 145),
            Token("1", 0.99, 880, 120, 895, 145),
            Token("组(普通组)", 0.99, 535, 150, 650, 175),
            Token("58 中西医临床医学(学制五年)(学费：5850元/年)", 0.99, 525, 190, 850, 215),
            Token("1419", 0.99, 880, 190, 970, 215),
        ],
    )
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5], "description": [0.05, 0.42],
                "numeric": [0.42, 0.5], "score_split": 0.455,
            },
            "right": {
                "page": [0.5, 1.0], "description": [0.51, 0.86],
                "numeric": [0.86, 1.0], "score_split": 0.915,
            },
        },
        "school_pattern": r"^(?P<institution_code>\d{4})\s*(?P<institution_name>.+?)\s*第(?P<group_code>[0-9A-Za-z]{3})组[（(](?P<group_name>.+?)[）)]$",
        "major_pattern": r"^(?P<major_code>[0-9A-Za-z]{2})\s*(?P<major_name>.+)$",
        "course_pattern": r"^(历史类|物理类)$",
        "subject_pattern": r"再选\s*[:：]\s*(.+)$",
        "required": ["科类", "再选科目", "院校代码", "院校专业组代码", "专业代码", "计划数", "分数线"],
        "integer_fields": ["专业组计划数", "计划数", "分数线"],
    }
    records = parse_two_column_plan_balance_score(
        [page], profile, y_tolerance_ratio=0.65
    )
    assert len(records) == 2
    assert records[0].values["院校专业组代码"] == "1435G15"
    assert records[0].values["科类"] == "历史类"
    assert records[0].values["再选科目"] == "不限"
    assert records[0].values["专业名称及专业方向"] == "外国语言文学类(英语和翻译)"
    assert records[0].values["学费"] == "6000"
    assert records[0].values["备注"] == "(新增计划；学费：6000元/年)"
    assert records[0].values["分数线"] == "594"
    assert records[1].values["科类"] == "物理类"
    assert records[1].values["再选科目"] == "生物"
    assert records[1].values["学制"] == "5"
    assert records[1].values["计划数"] == "1"
    assert records[1].values["分数线"] == "419"
    assert not records[0].issues
    assert not records[1].issues


def test_two_column_balance_parser_recovers_right_edge_group_with_embedded_plan() -> None:
    page = ExtractedPage(
        source_relative_path="henan/2026/demo.pdf",
        source_sha256="abc",
        page_number=1,
        method="pdf_page_ocr",
        width=1000,
        height=1400,
        tokens=[
            Token("\u7269\u7406\u7c7b", 0.99, 600, 40, 680, 65),
            Token("\u518d\u9009\uff1a\u5316\u5b66", 0.99, 800, 70, 900, 95),
            Token("6605 \u6d4b\u8bd5\u5b66\u9662 \u7b2c101 52\u7ec4(\u666e\u901a\u7ec4)", 0.99, 500, 120, 850, 145),
            Token("01 \u8ba1\u7b97\u673a\u79d1\u5b66\u4e0e\u6280\u672f(\u5b66\u8d39\uff1a29000\u5143/\u5e74)", 0.99, 520, 160, 850, 185),
            Token("5 389", 0.99, 880, 160, 970, 185),
        ],
    )
    profile = {
        "page_regions": {
            "left": {
                "page": [0.0, 0.5], "description": [0.05, 0.42],
                "numeric": [0.42, 0.5], "score_split": 0.455,
            },
            "right": {
                "page": [0.5, 1.0], "description": [0.5, 0.86],
                "numeric": [0.86, 1.0], "score_split": 0.915,
            },
        },
        "school_pattern": r"^(?P<institution_code>\d{4})\s*(?P<institution_name>.+?)\s*\u7b2c(?P<group_code>[0-9A-Za-z]{3})\u7ec4[\uff08(](?P<group_name>.+?)[\uff09)]$",
        "major_pattern": r"^(?P<major_code>[0-9A-Za-z]{2})\s*(?P<major_name>.+)$",
        "course_pattern": r"(\u5386\u53f2\u7c7b|\u7269\u7406\u7c7b)",
        "subject_pattern": r"\u518d\u9009\s*[:\uff1a]\s*(.+)$",
        "required": ["\u79d1\u7c7b", "\u518d\u9009\u79d1\u76ee", "\u9662\u6821\u4ee3\u7801", "\u9662\u6821\u4e13\u4e1a\u7ec4\u4ee3\u7801", "\u4e13\u4e1a\u4ee3\u7801", "\u8ba1\u5212\u6570", "\u5206\u6570\u7ebf"],
        "integer_fields": ["\u4e13\u4e1a\u7ec4\u8ba1\u5212\u6570", "\u8ba1\u5212\u6570", "\u5206\u6570\u7ebf"],
    }

    records = parse_two_column_plan_balance_score(
        [page], profile, y_tolerance_ratio=0.65
    )

    assert len(records) == 1
    assert records[0].values["\u9662\u6821\u4e13\u4e1a\u7ec4\u4ee3\u7801"] == "6605101"
    assert records[0].values["\u4e13\u4e1a\u7ec4\u8ba1\u5212\u6570"] == "52"
    assert records[0].values["\u4e13\u4e1a\u4ee3\u7801"] == "01"
    assert records[0].values["\u8ba1\u5212\u6570"] == "5"
    assert records[0].values["\u5206\u6570\u7ebf"] == "389"


def test_group_total_recheck_keeps_reused_group_codes_separate_by_context() -> None:
    records = [
        ParsedRecord(values={
            "来源文件": "demo.pdf", "科类": "历史类", "再选科目": "地理",
            "院校专业组代码": "6800102", "专业组计划数": "5", "计划数": "5",
        }),
        ParsedRecord(values={
            "来源文件": "demo.pdf", "科类": "物理类", "再选科目": "地理",
            "院校专业组代码": "6800102", "专业组计划数": "1", "计划数": "1",
        }),
        ParsedRecord(values={
            "来源文件": "demo.pdf", "科类": "物理类", "再选科目": "化学",
            "院校专业组代码": "9999101", "专业组计划数": "2", "计划数": "1",
        }),
    ]

    recheck_plan_totals(records)

    assert not records[0].issues
    assert not records[1].issues
    assert records[2].issues == ["专业计划数合计 1 与专业组计划数 2 不一致"]
