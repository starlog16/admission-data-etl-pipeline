from pathlib import Path

from admission_pipeline.profiles import select_profile


def test_high_priority_matching_profile_wins() -> None:
    profiles = [
        {"name": "anhui", "match": {"path_contains": ["anhui/2026"]}},
        {"name": "fallback", "match": {}},
    ]
    selected = select_profile(profiles, Path("anhui/2026/task/a.jpg"))
    assert selected["name"] == "anhui"


def test_vocational_rank_does_not_match_first_round_profile() -> None:
    profiles = [
        {
            "name": "first-round",
            "priority": 100,
            "match": {"path_contains": ["anhui/2026", "首轮", "最低分", "名次"]},
        },
        {
            "name": "vocational-rank",
            "priority": 110,
            "match": {
                "path_contains": [
                    "anhui/2026",
                    "普通高职（专科）提前批院校投档最低分及名次",
                ]
            },
        },
        {"name": "fallback", "priority": -1000, "match": {}},
    ]
    selected = select_profile(
        profiles,
        Path("anhui/2026/19安徽/普通高职（专科）提前批院校投档最低分及名次/6.png"),
    )
    assert selected["name"] == "vocational-rank"


def test_local_special_uses_narrow_table_profile() -> None:
    profiles = [
        {
            "name": "national",
            "priority": 100,
            "match": {"path_contains": ["anhui/2026", "国家专项计划首轮"]},
        },
        {
            "name": "local",
            "priority": 120,
            "match": {"path_contains": ["anhui/2026", "地方专项计划首轮"]},
        },
        {"name": "fallback", "priority": -1000, "match": {}},
    ]
    selected = select_profile(
        profiles,
        Path("anhui/2026/19安徽/安徽省2026年地方专项计划首轮投档最低分及名次/1.jpg"),
    )
    assert selected["name"] == "local"


def test_profile_can_match_extracted_document_text() -> None:
    profiles = [
        {
            "name": "two-column-plan",
            "priority": 90,
            "match": {
                "text_contains": [
                    "普通高校招生征集志愿计划",
                    "学校(专业及专业方向)",
                ]
            },
        },
        {"name": "fallback", "priority": -1000, "match": {}},
    ]
    selected = select_profile(
        profiles,
        Path("hebei/2026/27223.pdf"),
        document_text="2026年河北省普通高校招生征集志愿计划\n代号 学校(专业及专业方向)",
    )
    assert selected["name"] == "two-column-plan"


def test_profile_text_pattern_accepts_numbered_recruitment_round() -> None:
    profiles = [
        {
            "name": "two-column-plan",
            "priority": 90,
            "match": {
                "text_contains": ["学校(专业及专业方向)", "再选科目", "计划学制学费"],
                "text_patterns": [
                    r"普通高校招生(?:第[一二三四五六七八九十0-9]+次|再次)?征集志愿计划"
                ],
            },
        },
        {"name": "fallback", "priority": -1000, "match": {}},
    ]

    selected = select_profile(
        profiles,
        Path("hebei/2026/27242.pdf"),
        document_text=(
            "2026年河北省普通高校招生第二次征集志愿计划\n"
            "代号 学校(专业及专业方向) 名称与说明 再选科目 计划 学制 学费"
        ),
    )

    assert selected["name"] == "two-column-plan"
