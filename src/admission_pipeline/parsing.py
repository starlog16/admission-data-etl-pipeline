from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from typing import Any

from .domain import ExtractedPage, ParsedRecord, Token


PROVINCE_NAMES = {
    "beijing": "北京", "tianjin": "天津", "hebei": "河北", "shanxi": "山西",
    "inner_mongolia": "内蒙古", "liaoning": "辽宁", "jilin": "吉林",
    "heilongjiang": "黑龙江", "shanghai": "上海", "jiangsu": "江苏",
    "zhejiang": "浙江", "anhui": "安徽", "fujian": "福建", "jiangxi": "江西",
    "shandong": "山东", "henan": "河南", "hubei": "湖北", "hunan": "湖南",
    "guangdong": "广东", "guangxi": "广西", "hainan": "海南", "chongqing": "重庆",
    "sichuan": "四川", "guizhou": "贵州", "yunnan": "云南", "tibet": "西藏",
    "shaanxi": "陕西", "gansu": "甘肃", "qinghai": "青海", "ningxia": "宁夏",
    "xinjiang": "新疆",
}


def _cluster_rows(tokens: list[Token], tolerance_ratio: float) -> list[list[Token]]:
    if not tokens:
        return []
    median_height = statistics.median(token.height for token in tokens)
    tolerance = max(2.0, median_height * tolerance_ratio)
    rows: list[list[Token]] = []
    row_centers: list[float] = []
    for token in sorted(tokens, key=lambda value: (value.cy, value.x1)):
        best_index = None
        best_distance = None
        for index, center in enumerate(row_centers):
            distance = abs(token.cy - center)
            if distance <= tolerance and (best_distance is None or distance < best_distance):
                best_index = index
                best_distance = distance
        if best_index is None:
            rows.append([token])
            row_centers.append(token.cy)
        else:
            rows[best_index].append(token)
            row_centers[best_index] = statistics.mean(item.cy for item in rows[best_index])
    ordered = sorted(zip(row_centers, rows, strict=True), key=lambda value: value[0])
    return [sorted(row, key=lambda token: token.x1) for _, row in ordered]


def _join_tokens(tokens: Iterable[Token]) -> str:
    return "".join(token.text.strip() for token in tokens if token.text.strip()).strip()


def _strip_cjk_noise_from_typed_value(value: str, declared_type: str) -> str:
    """Remove watermark-like CJK text only when one unambiguous typed token remains."""
    if declared_type == "integer":
        match = re.fullmatch(r"[\u3400-\u9fff]*([0-9]+)[\u3400-\u9fff]*", value)
        return match.group(1) if match else value
    if declared_type == "code":
        match = re.fullmatch(r"[\u3400-\u9fff]*([0-9A-Za-z]+)[\u3400-\u9fff]*", value)
        return match.group(1) if match else value
    return value


def _validate_type(value: str, declared_type: str) -> str | None:
    if not value or declared_type == "text":
        return None
    if declared_type == "integer" and not re.fullmatch(r"\d+", value):
        return "应为整数"
    if declared_type == "code" and not re.fullmatch(r"[0-9A-Za-z]+", value):
        return "代码格式异常"
    return None


def _extract_document_context(
    pages: list[ExtractedPage], profile: dict[str, Any]
) -> dict[str, str]:
    context = {str(key): str(value) for key, value in profile.get("constants", {}).items()}
    texts = [token.text.strip() for page in pages for token in page.tokens if token.text.strip()]
    compact_text = re.sub(r"\s+", "", "\n".join(texts))

    source_parts: list[str] = []
    if pages:
        source_parts = pages[0].source_relative_path.replace("\\", "/").split("/")
    for part in source_parts:
        lowered = part.casefold().replace("-", "_")
        if lowered in PROVINCE_NAMES:
            context.setdefault("省份", PROVINCE_NAMES[lowered])
        if re.fullmatch(r"20\d{2}", part):
            context.setdefault("年份", part)

    year_match = re.search(r"(?P<year>20\d{2})年", compact_text)
    if year_match:
        context["年份"] = year_match.group("year")
    title_text = compact_text[:500]
    aliases = {
        "新疆维吾尔自治区": "新疆", "广西壮族自治区": "广西",
        "宁夏回族自治区": "宁夏", "西藏自治区": "西藏",
        "内蒙古自治区": "内蒙古", "北京市": "北京", "天津市": "天津",
        "上海市": "上海", "重庆市": "重庆",
        **{f"{name}省": name for name in PROVINCE_NAMES.values()},
    }
    for alias, normalized in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in title_text:
            context["省份"] = normalized
            break

    for field, pattern_text in (profile.get("context_patterns") or {}).items():
        match = re.search(str(pattern_text), compact_text)
        if not match:
            continue
        if "value" in match.groupdict():
            value = match.group("value")
        elif match.groups():
            value = match.group(1)
        else:
            value = match.group(0)
        context[str(field)] = value
    return context


def parse_spatial_rows(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    records: list[ParsedRecord] = []
    columns = profile["columns"]
    required = set(profile.get("required", []))
    minimum_fields = int(profile.get("minimum_populated_fields", 1))
    skip_markers = profile.get("skip_if_contains", [])
    fill_down_fields = profile.get("fill_down", [])
    previous: dict[str, str] = {}
    context = _extract_document_context(pages, profile)
    section_pattern = (
        re.compile(str(profile["section_pattern"]))
        if profile.get("section_pattern")
        else None
    )
    section_field = str(profile.get("section_field", "计划类别"))
    current_section = ""
    row_key = profile.get("row_key") or {}
    row_key_field = str(row_key.get("field", ""))
    row_key_pattern = (
        re.compile(str(row_key["pattern"])) if row_key.get("pattern") else None
    )
    continuation_field = str(profile.get("continuation_field", ""))

    for page in pages:
        for row_number, row in enumerate(
            _cluster_rows(page.tokens, y_tolerance_ratio), start=1
        ):
            joined_row = _join_tokens(row)
            if not joined_row or any(marker in joined_row for marker in skip_markers):
                continue
            if section_pattern and section_pattern.fullmatch(joined_row):
                current_section = joined_row
                continue
            values: dict[str, Any] = dict(context)
            issues: list[str] = []
            for column in columns:
                name = column["name"]
                selected = [
                    token
                    for token in row
                    if float(column["x_min"]) <= token.cx / page.width < float(column["x_max"])
                ]
                value = _join_tokens(selected)
                value = _strip_cjk_noise_from_typed_value(
                    value, column.get("type", "text")
                )
                if not value and name in fill_down_fields:
                    value = previous.get(name, "")
                if value:
                    previous[name] = value
                values[name] = value
                type_issue = _validate_type(value, column.get("type", "text"))
                if type_issue:
                    issues.append(f"{name}：{type_issue}（{value}）")

            for rule in profile.get("derive_fields", []):
                source_value = str(values.get(str(rule["source"]), ""))
                match = re.search(str(rule["pattern"]), source_value)
                if not match:
                    continue
                for index, field in enumerate(rule.get("fields", []), start=1):
                    values[str(field)] = match.group(index)

            if current_section:
                values[section_field] = current_section
            populated = sum(bool(values.get(column["name"])) for column in columns)
            if continuation_field and populated < minimum_fields and records:
                continuation = str(values.get(continuation_field, "")).strip()
                if continuation:
                    old = str(records[-1].values.get(continuation_field, "")).strip()
                    records[-1].values[continuation_field] = old + continuation
                    records[-1].values["原始行文本"] += " | " + joined_row
                continue
            if populated < minimum_fields:
                continue
            if row_key_pattern and not row_key_pattern.fullmatch(
                str(values.get(row_key_field, "")).strip()
            ):
                continue
            missing = [name for name in required if not values.get(name)]
            if missing:
                issues.append("缺少必填字段：" + "、".join(sorted(missing)))
            if profile.get("review_all", False):
                issues.append("未匹配到已验证版式，需要人工复核")

            values.update(
                {
                    "来源文件": page.source_relative_path,
                    "来源页": page.page_number,
                    "来源行": row_number,
                    "提取方式": page.method,
                    "原始行文本": joined_row,
                }
            )
            records.append(ParsedRecord(values=values, issues=issues))
    return records


def _tokens_in_x_band(
    row: list[Token], page_width: float, band: list[float]
) -> list[Token]:
    lower, upper = (float(value) for value in band)
    return [token for token in row if lower <= token.x1 / page_width < upper]


def _split_embedded_subject(
    value: str, subject_keywords: list[str]
) -> tuple[str, str]:
    for subject in sorted(subject_keywords, key=len, reverse=True):
        match = re.fullmatch(rf"(.+?)\s+({re.escape(subject)})", value)
        if match:
            return match.group(1).strip(), match.group(2)
    return value.strip(), ""


def _split_institution_full_name(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+?)[（(]([㐀-鿿]{2,12})[）)]", value.strip())
    if not match:
        return value.strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _extract_plan_context(
    pages: list[ExtractedPage], profile: dict[str, Any]
) -> dict[str, str]:
    context = _extract_document_context(pages, profile)
    texts = [token.text.strip() for page in pages for token in page.tokens if token.text.strip()]
    document_text = "\n".join(texts)
    year_match = re.search(r"(?P<year>\d{4})年", document_text)
    if year_match:
        context["年份"] = year_match.group("year")
    province_match = re.search(r"\d{4}年(?P<province>[\u3400-\u9fff]{1,8}?省)", document_text)
    if province_match:
        context["省份"] = province_match.group("province").removesuffix("省")
    for text in texts:
        parts = [part.strip() for part in text.split("/")]
        if len(parts) >= 3 and any("科目组合" in part for part in parts):
            context["招生类型"] = parts[0]
            context["批次"] = parts[1]
            context["科类"] = "/".join(parts[2:])
            break
    return context


def parse_two_column_hierarchical_plan(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    """Parse two-up recruitment plans with school rows and indented major rows."""
    records: list[ParsedRecord] = []
    context = _extract_plan_context(pages, profile)
    regions = profile["page_regions"]
    skip_markers = profile.get("skip_if_contains", [])
    subjects = [str(value) for value in profile.get("subject_keywords", [])]
    required = [str(value) for value in profile.get("required", [])]
    integer_fields = [str(value) for value in profile.get("integer_fields", [])]
    code_patterns = {
        str(key): re.compile(str(value))
        for key, value in profile.get("code_patterns", {}).items()
    }
    school_pattern = re.compile(profile.get("school_pattern", r"^(\d{4})\s*(.+)$"))
    major_pattern = re.compile(profile.get("major_pattern", r"^([0-9A-Za-z]{2})\s*(.+)$"))
    nature_pattern = re.compile(r"[\[【](公办|民办|独立学院)[\]】]")

    current_school: dict[str, str] | None = None
    current_major: dict[str, Any] | None = None
    school_had_major = False

    def finalize_major() -> None:
        nonlocal current_major, school_had_major
        if current_major is None:
            return
        school = current_school or {}
        values: dict[str, Any] = {
            **context,
            "院校代码": school.get("院校代码", ""),
            "院校名称": school.get("院校名称", ""),
            "院校性质": school.get("院校性质", ""),
            "院校计划总数": school.get("院校计划总数", ""),
            "专业代码": current_major["专业代码"],
            "专业名称及专业方向": current_major["专业名称及专业方向"],
            "再选科目": current_major["再选科目"],
            "计划数": current_major["计划数"],
            "学制": current_major["学制"],
            "学费": current_major["学费"],
            "备注": current_major["备注"],
            "来源文件": current_major["来源文件"],
            "来源页": current_major["来源页"],
            "来源行": current_major["来源行"],
            "提取方式": current_major["提取方式"],
            "原始行文本": current_major["原始行文本"],
            "_major_side": current_major["_major_side"],
            "_major_page": current_major["_major_page"],
            "_major_y1": current_major["_major_y1"],
            "_major_y2": current_major["_major_y2"],
            "_school_side": school.get("_school_side", ""),
            "_school_page": school.get("_school_page", ""),
            "_school_y1": school.get("_school_y1", ""),
            "_school_y2": school.get("_school_y2", ""),
        }
        issues = list(current_major["issues"])
        for field in required:
            if not str(values.get(field, "")).strip():
                issues.append(f"缺少必填字段：{field}")
        for field in integer_fields:
            value = str(values.get(field, "")).strip()
            if value and not re.fullmatch(r"\d+", value):
                issues.append(f"{field}：应为整数（{value}）")
        for field, pattern in code_patterns.items():
            value = str(values.get(field, "")).strip()
            if value and not pattern.fullmatch(value):
                issues.append(f"{field}：代码格式异常（{value}）")
        records.append(ParsedRecord(values=values, issues=list(dict.fromkeys(issues))))
        school_had_major = True
        current_major = None

    def add_orphan_school_issue() -> None:
        if current_school is None or school_had_major:
            return
        values: dict[str, Any] = {
            **context,
            **current_school,
            "专业代码": "",
            "专业名称及专业方向": "",
            "再选科目": "",
            "计划数": "",
            "学制": "",
            "学费": "",
            "备注": "",
            "来源文件": current_school["来源文件"],
            "来源页": current_school["来源页"],
            "来源行": current_school["来源行"],
            "提取方式": current_school["提取方式"],
            "原始行文本": current_school["原始行文本"],
        }
        records.append(
            ParsedRecord(values=values, issues=["识别到院校，但未识别到该院校的专业行"])
        )

    visual_row_number = 0
    for page in pages:
        for side in ("left", "right"):
            side_config = regions[side]
            side_min, side_max = (float(value) for value in side_config["page"])
            side_tokens = [
                token
                for token in page.tokens
                if side_min <= token.x1 / page.width < side_max
            ]
            for row in _cluster_rows(side_tokens, y_tolerance_ratio):
                visual_row_number += 1
                joined_row = _join_tokens(row)
                if not joined_row or any(marker in joined_row for marker in skip_markers):
                    continue
                description = _join_tokens(
                    _tokens_in_x_band(row, page.width, side_config["description"])
                )
                if not description:
                    continue
                if description.count("/") >= 2 and "科目组合" in description:
                    continue
                school_match = school_pattern.fullmatch(description)
                if school_match:
                    finalize_major()
                    add_orphan_school_issue()
                    raw_name = school_match.group(2).strip()
                    nature_match = nature_pattern.search(raw_name)
                    current_school = {
                        "院校代码": school_match.group(1),
                        "院校名称": nature_pattern.sub("", raw_name).strip(),
                        "院校性质": nature_match.group(1) if nature_match else "",
                        "院校计划总数": _join_tokens(
                            _tokens_in_x_band(row, page.width, side_config["plan"])
                        ),
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "_school_side": side,
                        "_school_page": page.page_number,
                        "_school_y1": min(token.y1 for token in row),
                        "_school_y2": max(token.y2 for token in row),
                    }
                    school_had_major = False
                    continue

                major_match = major_pattern.fullmatch(description)
                if major_match:
                    finalize_major()
                    name, embedded_subject = _split_embedded_subject(
                        major_match.group(2).strip(), subjects
                    )
                    explicit_subject = _join_tokens(
                        _tokens_in_x_band(row, page.width, side_config["subject"])
                    )
                    current_major = {
                        "专业代码": major_match.group(1),
                        "专业名称及专业方向": name,
                        "再选科目": explicit_subject or embedded_subject,
                        "计划数": _join_tokens(
                            _tokens_in_x_band(row, page.width, side_config["plan"])
                        ),
                        "学制": _join_tokens(
                            _tokens_in_x_band(row, page.width, side_config["duration"])
                        ),
                        "学费": _join_tokens(
                            _tokens_in_x_band(row, page.width, side_config["tuition"])
                        ),
                        "备注": "",
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "issues": [],
                        "in_note": False,
                        "_major_side": side,
                        "_major_page": page.page_number,
                        "_major_y1": min(token.y1 for token in row),
                        "_major_y2": max(token.y2 for token in row),
                    }
                    if current_school is None:
                        current_major["issues"].append("专业行之前未识别到所属院校")
                    continue

                if current_major is not None:
                    if description.startswith(("[", "【")) or current_major["in_note"]:
                        current_major["备注"] += description
                        current_major["in_note"] = True
                    else:
                        current_major["专业名称及专业方向"] += description
                    current_major["原始行文本"] += " | " + joined_row
                elif current_school is not None:
                    current_school["院校名称"] += description
                    nature_match = nature_pattern.search(current_school["院校名称"])
                    if nature_match:
                        current_school["院校性质"] = nature_match.group(1)
                        current_school["院校名称"] = nature_pattern.sub(
                            "", current_school["院校名称"]
                        ).strip()
                    current_school["原始行文本"] += " | " + joined_row

    finalize_major()
    add_orphan_school_issue()

    recheck_plan_totals(records)
    return records


def parse_hierarchical_group_major_plan(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    """Parse a school -> major-group -> major plan with wrapped descriptions."""
    context = _extract_document_context(pages, profile)
    bands = profile["bands"]
    skip_markers = [str(value) for value in profile.get("skip_if_contains", [])]
    required = [str(value) for value in profile.get("required", [])]
    subject_keywords = sorted(
        [str(value) for value in profile.get("subject_keywords", [])],
        key=len,
        reverse=True,
    )
    current_school: dict[str, str] | None = None
    current_group: dict[str, str] | None = None
    current_major: dict[str, str] | None = None
    records: list[ParsedRecord] = []
    row_number = 0

    def band(row: list[Token], page: ExtractedPage, name: str) -> str:
        return _join_tokens(_tokens_in_x_band(row, page.width, bands[name]))

    def finalize_major() -> None:
        nonlocal current_major
        if current_major is None:
            return
        school = current_school or {}
        group = current_group or {}
        institution_full_name = school.get("院校名称", "")
        institution_name, institution_region = _split_institution_full_name(
            institution_full_name
        )
        values: dict[str, Any] = {
            **context,
            "院校代码": school.get("院校代码", "") or group.get("院校代码", ""),
            "院校全称": institution_full_name,
            "院校名称": institution_name,
            "院校所在地区": institution_region,
            "院校计划总数": school.get("院校计划总数", ""),
            "院校专业组代码": group.get("院校专业组代码", ""),
            "专业组代码": group.get("专业组代码", ""),
            "科目要求": group.get("科目要求", ""),
            "院校专业组名称": group.get("院校专业组名称", ""),
            "专业组计划数": group.get("专业组计划数", ""),
            **current_major,
        }
        values["专业名称及招考方向"] = values.get("专业名称及专业方向", "")
        issues: list[str] = list(current_major.get("_issues", []))
        for field in required:
            if not str(values.get(field, "")).strip():
                issues.append(f"缺少必填字段：{field}")
        for field in profile.get("integer_fields", []):
            value = str(values.get(field, "")).strip()
            if value and not value.isdigit():
                issues.append(f"{field}：应为整数（{value}）")
        records.append(ParsedRecord(values=values, issues=issues))
        current_major = None

    for page in pages:
        for row in _cluster_rows(page.tokens, y_tolerance_ratio):
            row_number += 1
            joined = _join_tokens(row)
            if not joined or any(marker in joined for marker in skip_markers):
                continue
            identity = band(row, page, "identity")
            description = band(row, page, "description")
            pre_description = band(row, page, "pre_description")
            plan = band(row, page, "plan")
            remark = band(row, page, "remark")
            group_pattern = str(profile.get("group_pattern", r"\d{6}"))
            major_pattern = str(profile.get("major_pattern", r"[0-9A-Za-z]{2}"))
            group_match = re.fullmatch(group_pattern, identity)
            embedded_group = re.fullmatch(rf"({group_pattern})(.+)", pre_description)
            group_code = group_match.group(0) if group_match else (
                embedded_group.group(1) if embedded_group else ""
            )
            major_match = re.fullmatch(major_pattern, identity)
            embedded_major = (
                re.fullmatch(rf"({major_pattern})(.+)", pre_description)
                if current_group is not None
                else None
            )
            major_code = major_match.group(0) if major_match else (
                embedded_major.group(1) if embedded_major else ""
            )

            if group_code:
                finalize_major()
                institution_length = int(profile.get("institution_code_length", 4))
                group_subject = band(row, page, "subject")
                group_name = description
                if embedded_group and not group_name:
                    remainder = embedded_group.group(2)
                    for keyword in subject_keywords:
                        if remainder.startswith(keyword):
                            group_subject = keyword
                            remainder = remainder[len(keyword):]
                            break
                    group_name = remainder
                current_group = {
                    "院校代码": group_code[:institution_length],
                    "院校专业组代码": group_code,
                    "专业组代码": group_code[institution_length:],
                    "院校专业组名称": group_name,
                    "科目要求": group_subject,
                    "专业组计划数": plan,
                }
                continue

            if major_code and plan.isdigit():
                finalize_major()
                current_major = {
                    "专业代码": major_code,
                    "专业名称及专业方向": description or (
                        embedded_major.group(2) if embedded_major else ""
                    ),
                    "计划数": plan,
                    "学制": band(row, page, "duration"),
                    "收费标准": band(row, page, "tuition"),
                    "外语语种": band(row, page, "language"),
                    "是否口试": band(row, page, "oral"),
                    "备注": remark,
                    "来源文件": page.source_relative_path,
                    "来源页": str(page.page_number),
                    "来源行": str(row_number),
                    "提取方式": page.method,
                    "原始行文本": joined,
                    "_issues": [] if current_group is not None else ["专业行之前未识别到所属专业组"],
                }
                continue

            school_name = pre_description.strip()
            if school_name and plan.isdigit():
                finalize_major()
                current_school = {
                    "院校代码": "",
                    "院校名称": school_name,
                    "院校计划总数": plan,
                }
                current_group = None
                continue

            if current_major is not None and (description or remark):
                if description:
                    current_major["专业名称及专业方向"] += description
                if remark:
                    current_major["备注"] += remark
                current_major["原始行文本"] += " | " + joined
            elif current_group is not None and description:
                current_group["院校专业组名称"] += description
            elif current_school is not None and pre_description:
                current_school["院校名称"] += pre_description

    finalize_major()
    recheck_plan_totals(records)
    _recheck_group_plan_totals(records)
    return records


def _recheck_group_plan_totals(records: list[ParsedRecord]) -> None:
    grouped: dict[tuple[str, str, str], list[ParsedRecord]] = {}
    for record in records:
        key = (
            str(record.values.get("来源文件", "")),
            str(record.values.get("院校代码", "")),
            str(record.values.get("专业组代码", "")),
        )
        grouped.setdefault(key, []).append(record)
    for group_records in grouped.values():
        expected = str(group_records[0].values.get("专业组计划数", "")).strip()
        plans = [str(record.values.get("计划数", "")).strip() for record in group_records]
        if expected.isdigit() and plans and all(value.isdigit() for value in plans):
            actual = sum(int(value) for value in plans)
            if actual != int(expected):
                issue = f"专业计划数合计 {actual} 与专业组计划数 {expected} 不一致"
                for record in group_records:
                    if issue not in record.issues:
                        record.issues.append(issue)


def parse_flat_major_plan(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    """Parse a flat major-level plan whose remarks may wrap across visual rows."""
    context = _extract_document_context(pages, profile)
    bands = profile["bands"]
    skip_markers = [str(value) for value in profile.get("skip_if_contains", [])]
    required = [str(value) for value in profile.get("required", [])]
    subject_code_pattern = re.compile(
        str(profile.get("subject_code_pattern", r"(.+类)([0-9A-Za-z]+)"))
    )
    records: list[ParsedRecord] = []
    row_number = 0

    def band(row: list[Token], page: ExtractedPage, name: str) -> str:
        return _join_tokens(_tokens_in_x_band(row, page.width, bands[name]))

    for page in pages:
        for row in _cluster_rows(page.tokens, y_tolerance_ratio):
            row_number += 1
            joined = _join_tokens(row)
            if not joined or any(marker in joined for marker in skip_markers):
                continue
            sequence = band(row, page, "sequence")
            if not sequence.isdigit():
                continuation = band(row, page, "remark")
                if continuation and records:
                    records[-1].values["备注"] = str(records[-1].values.get("备注", "")) + continuation
                    records[-1].values["原始行文本"] += " | " + joined
                continue
            subject_and_code = band(row, page, "subject_code")
            match = subject_code_pattern.fullmatch(subject_and_code)
            subject = match.group(1) if match else ""
            institution_code = match.group(2) if match else ""
            values: dict[str, Any] = {
                **context,
                "序号": sequence,
                "科类": subject,
                "院校代码": institution_code,
                "院校名称": band(row, page, "institution_name"),
                "计划类别": band(row, page, "category"),
                "专业代码": band(row, page, "major_code"),
                "专业名称及专业方向": band(row, page, "major_name"),
                "分数线": band(row, page, "score_line"),
                "再选科目": band(row, page, "subject_requirement"),
                "计划数": band(row, page, "plan"),
                "外语语种": band(row, page, "language"),
                "学制": band(row, page, "duration"),
                "学费": band(row, page, "tuition"),
                "备注": band(row, page, "remark"),
                "来源文件": page.source_relative_path,
                "来源页": page.page_number,
                "来源行": row_number,
                "提取方式": page.method,
                "原始行文本": joined,
            }
            issues: list[str] = []
            for field in required:
                if not str(values.get(field, "")).strip():
                    issues.append(f"缺少必填字段：{field}")
            for field in profile.get("integer_fields", []):
                value = str(values.get(field, "")).strip()
                if value and not value.isdigit():
                    issues.append(f"{field}：应为整数（{value}）")
            records.append(ParsedRecord(values=values, issues=issues))
    return records


def _clean_table_cell(value: object) -> str:
    text = str(value or "").replace("\r", "\n")
    text = re.sub(r"\n+", "", text)
    text = re.sub(r"(?<=[㐀-鿿])\s+(?=[㐀-鿿])", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_structured_table(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    """Parse PDF-detected tables and original workbooks by stable column index."""
    if not any(page.tables for page in pages):
        fallback = profile.get("fallback_parser")
        if fallback == "spatial_rows":
            return parse_spatial_rows(pages, profile, y_tolerance_ratio=y_tolerance_ratio)
        if fallback == "flat_major_plan":
            return parse_flat_major_plan(pages, profile, y_tolerance_ratio=y_tolerance_ratio)
        return []

    context = _extract_document_context(pages, profile)
    column_map = {str(field): int(index) for field, index in profile["column_map"].items()}
    required = [str(value) for value in profile.get("required", [])]
    integer_fields = [str(value) for value in profile.get("integer_fields", [])]
    skip_markers = [str(value) for value in profile.get("skip_if_contains", [])]
    row_key = profile.get("row_key") or {}
    row_key_field = str(row_key.get("field", ""))
    row_key_pattern = re.compile(str(row_key["pattern"]))
    section_pattern = (
        re.compile(str(profile["section_pattern"]))
        if profile.get("section_pattern")
        else None
    )
    section_field = str(profile.get("section_field", "计划类别"))
    current_section = ""
    records: list[ParsedRecord] = []
    source_row = 0

    for page in pages:
        for table in page.tables:
            for row in table:
                source_row += 1
                cells = [_clean_table_cell(value) for value in row]
                joined = "|".join(cells)
                if not any(cells) or any(marker in joined for marker in skip_markers):
                    continue
                nonempty = [value for value in cells if value]
                if section_pattern and len(nonempty) == 1 and section_pattern.fullmatch(nonempty[0]):
                    current_section = nonempty[0]
                    continue
                values: dict[str, Any] = dict(context)
                for field, index in column_map.items():
                    values[field] = cells[index] if index < len(cells) else ""
                for selector_field, choices in (profile.get("dynamic_fields") or {}).items():
                    selected = (choices or {}).get(str(values.get(selector_field, "")), {})
                    for source_field, target_field in selected.items():
                        values[str(target_field)] = values.pop(str(source_field), "")
                if current_section:
                    values[section_field] = current_section
                if not row_key_pattern.fullmatch(str(values.get(row_key_field, "")).strip()):
                    continue
                for rule in profile.get("derive_fields", []):
                    source_value = str(values.get(str(rule["source"]), ""))
                    match = re.search(str(rule["pattern"]), source_value)
                    if match:
                        for index, field in enumerate(rule.get("fields", []), start=1):
                            values[str(field)] = match.group(index)
                for field in integer_fields:
                    values[field] = _strip_cjk_noise_from_typed_value(
                        str(values.get(field, "")), "integer"
                    )
                for field in profile.get("code_fields", []):
                    values[str(field)] = _strip_cjk_noise_from_typed_value(
                        str(values.get(str(field), "")), "code"
                    )
                values.update(
                    {
                        "来源文件": page.source_relative_path,
                        "来源页": page.page_number,
                        "来源工作表": page.sheet_name,
                        "来源行": source_row,
                        "提取方式": page.method,
                        "原始行文本": joined,
                    }
                )
                issues: list[str] = []
                for field in required:
                    if not str(values.get(field, "")).strip():
                        issues.append(f"缺少必填字段：{field}")
                for field in integer_fields:
                    value = str(values.get(field, "")).strip()
                    if value and not value.isdigit():
                        issues.append(f"{field}：应为整数（{value}）")
                records.append(ParsedRecord(values=values, issues=issues))
    return records


def recheck_plan_totals(records: list[ParsedRecord]) -> None:
    mismatch_prefix = "专业计划数合计 "
    for record in records:
        record.issues = [
            issue for issue in record.issues if not issue.startswith(mismatch_prefix)
        ]
    grouped: dict[tuple[str, str], list[ParsedRecord]] = {}
    for record in records:
        key = (str(record.values.get("来源文件", "")), str(record.values.get("院校代码", "")))
        grouped.setdefault(key, []).append(record)
    for school_records in grouped.values():
        expected = str(school_records[0].values.get("院校计划总数", "")).strip()
        plans = [str(record.values.get("计划数", "")).strip() for record in school_records]
        if expected.isdigit() and plans and all(plan.isdigit() for plan in plans):
            actual = sum(int(plan) for plan in plans)
            if actual != int(expected):
                issue = f"专业计划数合计 {actual} 与院校计划总数 {expected} 不一致"
                for record in school_records:
                    if issue not in record.issues:
                        record.issues.append(issue)


def parse_pages(
    pages: list[ExtractedPage], profile: dict[str, Any], *, y_tolerance_ratio: float
) -> list[ParsedRecord]:
    parser_name = profile.get("parser")
    if parser_name == "spatial_rows":
        return parse_spatial_rows(
            pages, profile, y_tolerance_ratio=y_tolerance_ratio
        )
    if parser_name == "two_column_hierarchical_plan":
        return parse_two_column_hierarchical_plan(
            pages, profile, y_tolerance_ratio=y_tolerance_ratio
        )
    if parser_name == "hierarchical_group_major_plan":
        return parse_hierarchical_group_major_plan(
            pages, profile, y_tolerance_ratio=y_tolerance_ratio
        )
    if parser_name == "flat_major_plan":
        return parse_flat_major_plan(
            pages, profile, y_tolerance_ratio=y_tolerance_ratio
        )
    if parser_name == "structured_table":
        return parse_structured_table(
            pages, profile, y_tolerance_ratio=y_tolerance_ratio
        )
    raise ValueError(f"不支持的解析器：{parser_name}")
