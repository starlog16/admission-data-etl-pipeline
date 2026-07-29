from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from typing import Any

from .domain import ExtractedPage, ParseDiagnostics, ParsedRecord, Token


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


def _strip_cjk_noise_from_integer_or_allowed(
    value: str,
    allowed_values: set[str],
    noise_chars: set[str] | None = None,
) -> str:
    """Clean watermark text around one integer or one explicitly allowed literal."""
    if value in allowed_values:
        return value
    if noise_chars:
        numeric_match = re.fullmatch(r"([^0-9]*)([0-9]+)([^0-9]*)", value)
        if numeric_match and all(
            char in noise_chars
            for char in numeric_match.group(1) + numeric_match.group(3)
        ):
            return numeric_match.group(2)
    else:
        numeric = _strip_cjk_noise_from_typed_value(value, "integer")
        if numeric != value:
            return numeric
    matches = []
    if noise_chars:
        matches = [
            allowed
            for allowed in allowed_values
            if (
                value.endswith(allowed)
                and all(char in noise_chars for char in value[: -len(allowed)])
            )
            or (
                value.startswith(allowed)
                and all(char in noise_chars for char in value[len(allowed) :])
            )
        ]
    return matches[0] if len(matches) == 1 else value


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
            context.setdefault("省份", normalized)
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

    source_text = "/".join(source_parts)
    for marker, value in (
        ("物理", "物理类"),
        ("历史", "历史类"),
        ("文史", "文史"),
        ("理工", "理工"),
    ):
        if marker in source_text:
            context.setdefault("科类", value)
            break
    round_text = f"{source_text}\n{title_text}"
    round_match = re.search(r"第\s*(?P<value>[一二三四五六七八九十0-9]+)\s*次", round_text)
    if round_match:
        value = round_match.group("value")
        number_map = {
            "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
            "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
        }
        context.setdefault("征集轮次", number_map.get(value, value))
    elif "再次征集" in round_text or "再次征求" in round_text:
        context.setdefault("征集轮次", "2")
    elif "征集志愿" in round_text or "征求志愿" in round_text:
        context.setdefault("征集轮次", "1")
    return context


def parse_spatial_rows(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
    diagnostics: ParseDiagnostics | None = None,
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
    critical_confidence = float(profile.get("critical_confidence", 0.88))
    detected_units = 0
    recovered_units = 0

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
            field_evidence: dict[str, dict[str, Any]] = {}
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
                declared_type = str(column.get("type", "text"))
                if declared_type in {"integer", "code"}:
                    score = min((token.score for token in selected), default=0.0)
                    status = (
                        "source_high_confidence"
                        if value and not type_issue and score >= critical_confidence
                        else "unresolved"
                        if value or name in required
                        else "absent_optional"
                    )
                    field_evidence[name] = {
                        "raw_value": value,
                        "raw_confidence": round(score, 5),
                        "final_value": value,
                        "status": status,
                    }
                    if value and not type_issue and score < critical_confidence:
                        issues.append(
                            f"{name}OCR置信度低于阈值（{score:.3f}<{critical_confidence:.3f}）"
                        )

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
            row_key_value = str(values.get(row_key_field, "")).strip()
            row_key_valid = not row_key_pattern or bool(
                row_key_pattern.fullmatch(row_key_value)
            )
            if not row_key_valid:
                typed_evidence = any(
                    str(values.get(str(column["name"]), "")).strip()
                    and str(column.get("type", "text")) in {"integer", "code"}
                    and bool(re.search(r"\d", str(values.get(str(column["name"]), ""))))
                    for column in columns
                )
                if not typed_evidence:
                    continue
                issues.append(
                    f"检测到视觉数据行，但行关键字段异常：{row_key_field}（{row_key_value or '空'}）"
                )
                recovered_units += 1
            detected_units += 1
            missing = [name for name in required if not values.get(name)]
            if missing:
                issues.append("缺少必填字段：" + "、".join(sorted(missing)))
            if profile.get("review_all", False):
                issues.append("未匹配到已验证版式，需要人工复核")
            values["_field_evidence"] = field_evidence

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
    if diagnostics is not None:
        diagnostics.coverage_observable = True
        diagnostics.source_units_detected = detected_units
        diagnostics.records_emitted = len(records)
        diagnostics.recovered_units = recovered_units
        diagnostics.unparsed_units = detected_units - len(records)
    return records


def _tokens_in_x_band(
    row: list[Token], page_width: float, band: list[float]
) -> list[Token]:
    lower, upper = (float(value) for value in band)
    # OCR boxes for long values (especially tuition) often extend a few pixels
    # into the previous narrow column. The center is the stable ownership signal;
    # using x1 silently shifted values such as 100000 from 学费 into 学制.
    return [token for token in row if lower <= token.cx / page_width < upper]


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
    diagnostics: ParseDiagnostics | None = None,
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
    recovered_major_pattern = re.compile(
        r"^(?P<major_code>[0-9A-Za-z]{1,3})\s*"
        r"(?P<major_name>[\u3400-\u9fff（(].+)$"
    )
    critical_confidence = float(profile.get("critical_confidence", 0.88))

    current_school: dict[str, str] | None = None
    current_major: dict[str, Any] | None = None
    school_had_major = False
    detected_units = 0
    recovered_units = 0
    def finalize_major() -> None:
        nonlocal current_major, school_had_major, recovered_units
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
        field_scores = {
            **school.get("_field_scores", {}),
            **current_major.get("_field_scores", {}),
        }
        field_evidence: dict[str, dict[str, Any]] = {}
        for field in set(code_patterns) | set(integer_fields) | {"学费"}:
            value = str(values.get(field, "")).strip()
            score = float(field_scores.get(field, 0.0))
            if field in code_patterns:
                valid = bool(code_patterns[field].fullmatch(value))
            elif field == "学费":
                valid = bool(re.fullmatch(r"(?:\d{3,6}|待定|免费)", value))
            else:
                valid = value.isdigit()
            status = (
                "source_high_confidence"
                if value and valid and score >= critical_confidence
                else "unresolved"
                if value or field in required
                else "absent_optional"
            )
            field_evidence[field] = {
                "raw_value": value,
                "raw_confidence": round(score, 5),
                "final_value": value,
                "status": status,
            }
            if value and valid and score < critical_confidence:
                issues.append(
                    f"{field}OCR置信度低于阈值（{score:.3f}<{critical_confidence:.3f}）"
                )
        values["_field_evidence"] = field_evidence
        records.append(ParsedRecord(values=values, issues=list(dict.fromkeys(issues))))
        if current_major.get("_recovered", False):
            recovered_units += 1
        school_had_major = True
        current_major = None

    def add_orphan_school_issue() -> None:
        nonlocal detected_units, recovered_units
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
        detected_units += 1
        recovered_units += 1

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
                if min(token.cy for token in row) / page.height >= float(
                    profile.get("content_bottom_ratio", 0.92)
                ):
                    # Page numbers are frequently split across the narrow numeric
                    # columns (for example “第 1 页”) and otherwise look like a
                    # numeric-only major whose description OCR disappeared.
                    continue
                description_tokens = _tokens_in_x_band(
                    row, page.width, side_config["description"]
                )
                subject_tokens = _tokens_in_x_band(
                    row, page.width, side_config["subject"]
                )
                plan_tokens = _tokens_in_x_band(
                    row, page.width, side_config["plan"]
                )
                duration_tokens = _tokens_in_x_band(
                    row, page.width, side_config["duration"]
                )
                tuition_tokens = _tokens_in_x_band(
                    row, page.width, side_config["tuition"]
                )
                description = _join_tokens(description_tokens)
                explicit_subject = _join_tokens(subject_tokens)
                plan_value = _join_tokens(plan_tokens)
                duration_value = _join_tokens(duration_tokens)
                tuition_value = _join_tokens(tuition_tokens)
                subject_footprint = any(
                    keyword in explicit_subject for keyword in subjects
                )
                has_field_footprint = bool(
                    subject_footprint or plan_value or duration_value or tuition_value
                )
                if not description and not has_field_footprint:
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
                        "院校计划总数": plan_value,
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "_school_side": side,
                        "_school_page": page.page_number,
                        "_school_y1": min(token.y1 for token in row),
                        "_school_y2": max(token.y2 for token in row),
                        "_field_scores": {
                            "院校代码": min(
                                (token.score for token in description_tokens),
                                default=0.0,
                            ),
                            "院校计划总数": min(
                                (token.score for token in plan_tokens),
                                default=0.0,
                            ),
                        },
                    }
                    school_had_major = False
                    continue

                major_match = major_pattern.fullmatch(description)
                recovered_major_match = (
                    recovered_major_pattern.fullmatch(description)
                    if not major_match
                    else None
                )
                looks_like_damaged_major = bool(
                    current_school is not None
                    and not description.startswith(("[", "【"))
                    and (
                        recovered_major_match
                        or subject_footprint
                        or plan_value.isdigit()
                        or duration_value.isdigit()
                        or tuition_value.isdigit()
                        or tuition_value in {"待定", "免费"}
                    )
                )
                if major_match or looks_like_damaged_major:
                    # Count the visual source candidate before a record is
                    # finalized so completeness is not inferred from output.
                    detected_units += 1
                    finalize_major()
                    recovered = major_match is None
                    raw_major_code = (
                        major_match.group(1)
                        if major_match
                        else recovered_major_match.group("major_code")
                        if recovered_major_match
                        else ""
                    )
                    raw_major_name = (
                        major_match.group(2)
                        if major_match
                        else recovered_major_match.group("major_name")
                        if recovered_major_match
                        else description
                    )
                    name, embedded_subject = _split_embedded_subject(
                        raw_major_name.strip(), subjects
                    )
                    current_major = {
                        "专业代码": raw_major_code,
                        "专业名称及专业方向": name,
                        "再选科目": explicit_subject or embedded_subject,
                        "计划数": plan_value,
                        "学制": duration_value,
                        "学费": tuition_value,
                        "备注": "",
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "issues": (
                            ["专业行结构已保留，但专业代码锚点异常"]
                            if recovered
                            else []
                        ),
                        "in_note": False,
                        "_major_side": side,
                        "_major_page": page.page_number,
                        "_major_y1": min(token.y1 for token in row),
                        "_major_y2": max(token.y2 for token in row),
                        "_recovered": recovered,
                        "_field_scores": {
                            "专业代码": min(
                                (token.score for token in description_tokens),
                                default=0.0,
                            ),
                            "计划数": min(
                                (token.score for token in plan_tokens),
                                default=0.0,
                            ),
                            "学制": min(
                                (token.score for token in duration_tokens),
                                default=0.0,
                            ),
                            "学费": min(
                                (token.score for token in tuition_tokens),
                                default=0.0,
                            ),
                        },
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
    records.sort(
        key=lambda record: (
            str(record.values.get("来源文件", "")),
            int(record.values.get("来源页", 0) or 0),
            0
            if (
                record.values.get("_major_side")
                or record.values.get("_school_side")
            )
            == "left"
            else 1,
            int(record.values.get("来源行", 0) or 0),
        )
    )
    if diagnostics is not None:
        diagnostics.coverage_observable = True
        diagnostics.source_units_detected = detected_units
        diagnostics.records_emitted = len(records)
        diagnostics.recovered_units = recovered_units
        diagnostics.unparsed_units = detected_units - len(records)
    return records


def parse_two_column_plan_balance_score(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
) -> list[ParsedRecord]:
    """Parse two-up school/group -> major plans with plan balance and score line."""
    records: list[ParsedRecord] = []
    context = _extract_document_context(pages, profile)
    regions = profile["page_regions"]
    skip_markers = [str(value) for value in profile.get("skip_if_contains", [])]
    required = [str(value) for value in profile.get("required", [])]
    integer_fields = [str(value) for value in profile.get("integer_fields", [])]
    school_pattern = re.compile(str(profile["school_pattern"]))
    school_recovery_pattern = re.compile(
        r"^(?P<institution_code>\d{4})\s*(?P<institution_name>.+?)\s*"
        r"\u7b2c(?P<group_code>[0-9A-Za-z]{3})"
        r"(?:\s+(?P<embedded_plan>\d{1,3}))?\u7ec4[\uff08(]"
        r"(?P<group_name>.+?)(?:[\uff09)]|$)"
    )
    school_fallback_pattern = re.compile(r"^(?P<institution_code>\d{4})\s*(?P<institution_name>.+)$")
    major_pattern = re.compile(str(profile.get("major_pattern", r"^(?P<major_code>[0-9A-Za-z]{2})\s*(?P<major_name>.+)$")))
    course_pattern = re.compile(str(profile.get("course_pattern", r"^(历史类|物理类)$")))
    subject_pattern = re.compile(str(profile.get("subject_pattern", r"再选\s*[:：]\s*(.+)$")))
    critical_score = float(profile.get("minimum_critical_ocr_score", 0.0))

    current_course = ""
    current_subject = ""
    current_group: dict[str, Any] | None = None
    current_major: dict[str, Any] | None = None
    group_had_major = False
    visual_row_number = 0

    def normalize_current_group() -> None:
        if current_group is None or current_group.get("院校专业组代码"):
            return
        combined = str(current_group.get("院校代码", "")) + str(
            current_group.get("院校名称", "")
        )
        match = school_pattern.fullmatch(combined)
        recovered = False
        if not match:
            match = school_recovery_pattern.fullmatch(combined)
            recovered = match is not None
        if not match:
            return
        institution_code = match.group("institution_code")
        institution_name = match.group("institution_name").strip()
        group_code = match.group("group_code")
        group_name = match.group("group_name").strip()
        embedded_plan = match.groupdict().get("embedded_plan", "") if recovered else ""
        current_group.update(
            {
                "院校代码": institution_code,
                "院校名称": institution_name,
                "院校专业组代码": institution_code + group_code,
                "专业组代码": group_code,
                "院校专业组名称": f"{institution_name} 第{group_code}组({group_name})",
            }
        )
        if embedded_plan and not str(current_group.get("\u4e13\u4e1a\u7ec4\u8ba1\u5212\u6570", "")).strip():
            current_group["\u4e13\u4e1a\u7ec4\u8ba1\u5212\u6570"] = embedded_plan
        current_group["issues"] = [
            issue
            for issue in current_group.get("issues", [])
            if issue != "院校专业组结构未完整识别"
        ]

    def numeric_values(
        row: list[Token], page: ExtractedPage, side_config: dict[str, Any]
    ) -> tuple[str, str, float]:
        selected = _tokens_in_x_band(row, page.width, side_config["numeric"])
        found: list[tuple[str, float, float]] = []
        for token in selected:
            for value in re.findall(r"\d+", token.text):
                found.append((value, token.cx / page.width, token.score))
        if not found:
            return "", "", 1.0
        confidence = min(item[2] for item in found)
        if len(found) >= 2:
            return found[0][0], found[-1][0], confidence
        value, center, _ = found[0]
        if value.isdigit() and 4 <= len(value) <= 6:
            return value[:-3], value[-3:], confidence
        if center >= float(side_config["score_split"]):
            return "", value, confidence
        return value, "", confidence

    def split_major_details(value: str) -> tuple[str, str, str, str]:
        tuition_match = re.search(r"学费\s*[:：]\s*(\d{3,6})\s*元", value)
        duration_match = re.search(r"学制\s*[:：]?\s*(\d+)\s*年", value)
        if duration_match:
            duration = duration_match.group(1)
        else:
            chinese_duration = re.search(r"学制\s*([一二三四五六七八九十])\s*年", value)
            duration_map = {
                "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
            }
            duration = duration_map.get(chinese_duration.group(1), "") if chinese_duration else ""
        detail_match = re.search(r"[（(](?=(?:部分)?新增计划|学(?:费|制))", value)
        if detail_match:
            name = value[: detail_match.start()].strip()
            note = value[detail_match.start() :].strip()
        else:
            name = value.strip()
            note = ""
        return name, duration, tuition_match.group(1) if tuition_match else "", note

    def finalize_major() -> None:
        nonlocal current_major, group_had_major
        if current_major is None:
            return
        normalize_current_group()
        group = current_group or {}
        name, duration, tuition, note = split_major_details(
            str(current_major["专业描述"])
        )
        values: dict[str, Any] = {
            **context,
            "科类": current_major["科类"],
            "科目要求": current_major["再选科目"],
            "再选科目": current_major["再选科目"],
            "院校代码": group.get("院校代码", ""),
            "院校名称": group.get("院校名称", ""),
            "院校专业组代码": group.get("院校专业组代码", ""),
            "专业组代码": group.get("专业组代码", ""),
            "院校专业组名称": group.get("院校专业组名称", ""),
            "专业组计划数": group.get("专业组计划数", ""),
            "专业代码": current_major["专业代码"],
            "专业名称及专业方向": name,
            "计划数": current_major["计划数"],
            "分数线": current_major["分数线"],
            "学制": duration,
            "学费": tuition,
            "备注": note,
            "来源文件": current_major["来源文件"],
            "来源页": current_major["来源页"],
            "来源行": current_major["来源行"],
            "提取方式": current_major["提取方式"],
            "原始行文本": current_major["原始行文本"],
            "_major_side": current_major["_major_side"],
            "_major_page": current_major["_major_page"],
            "_major_y1": current_major["_major_y1"],
            "_major_y2": current_major["_major_y2"],
            "_group_side": group.get("_group_side", ""),
            "_group_page": group.get("_group_page", ""),
            "_group_y1": group.get("_group_y1", ""),
            "_group_y2": group.get("_group_y2", ""),
        }
        issues = list(group.get("issues", [])) + list(current_major["issues"])
        for field in required:
            if not str(values.get(field, "")).strip():
                issues.append(f"缺少必填字段：{field}")
        for field in integer_fields:
            value = str(values.get(field, "")).strip()
            if value and not re.fullmatch(r"\d+", value):
                issues.append(f"{field}：应为整数（{value}）")
        score = str(values.get("分数线", "")).strip()
        if score.isdigit() and not 0 <= int(score) <= 750:
            issues.append(f"分数线：超出合理范围（{score}）")
        records.append(ParsedRecord(values=values, issues=list(dict.fromkeys(issues))))
        group_had_major = True
        current_major = None

    def add_orphan_group_issue() -> None:
        normalize_current_group()
        if current_group is None or group_had_major:
            return
        values = {
            **context,
            "科类": current_course,
            "科目要求": current_subject,
            "再选科目": current_subject,
            **current_group,
            "专业代码": "",
            "专业名称及专业方向": "",
            "计划数": "",
            "分数线": "",
            "学制": "",
            "学费": "",
            "备注": "",
        }
        records.append(ParsedRecord(values=values, issues=["识别到院校专业组，但未识别到专业行"]))

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
                if not joined_row:
                    continue
                context_row = False
                course_match = course_pattern.search(joined_row)
                if course_match and not re.match(r"\d", joined_row):
                    current_course = course_match.group(1)
                    context_row = True
                subject_match = subject_pattern.search(joined_row)
                if subject_match:
                    current_subject = subject_match.group(1).strip()
                    context_row = True
                if context_row:
                    continue
                if any(marker in joined_row for marker in skip_markers):
                    continue
                description_tokens = _tokens_in_x_band(
                    row, page.width, side_config["description"]
                )
                description = _join_tokens(description_tokens)
                if not description:
                    continue
                plan, score, numeric_confidence = numeric_values(row, page, side_config)

                school_match = school_pattern.fullmatch(description)
                if school_match:
                    finalize_major()
                    add_orphan_group_issue()
                    institution_code = school_match.group("institution_code")
                    institution_name = school_match.group("institution_name").strip()
                    group_code = school_match.group("group_code")
                    group_name = school_match.group("group_name").strip()
                    issues: list[str] = []
                    if critical_score and min(token.score for token in description_tokens) < critical_score:
                        issues.append("院校专业组行 OCR 置信度较低")
                    current_group = {
                        "院校代码": institution_code,
                        "院校名称": institution_name,
                        "院校专业组代码": institution_code + group_code,
                        "专业组代码": group_code,
                        "院校专业组名称": f"{institution_name} 第{group_code}组({group_name})",
                        "专业组计划数": plan,
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "issues": issues,
                        "_group_side": side,
                        "_group_page": page.page_number,
                        "_group_y1": min(token.y1 for token in row),
                        "_group_y2": max(token.y2 for token in row),
                    }
                    group_had_major = False
                    continue

                fallback_match = school_fallback_pattern.fullmatch(description)
                looks_like_wrapped_numeric_note = bool(
                    re.match(r"^\d{4,6}(?:元|年|/)", description)
                )
                if fallback_match and not looks_like_wrapped_numeric_note:
                    finalize_major()
                    add_orphan_group_issue()
                    institution_code = fallback_match.group("institution_code")
                    institution_name = fallback_match.group("institution_name").strip()
                    current_group = {
                        "院校代码": institution_code,
                        "院校名称": institution_name,
                        "院校专业组代码": "",
                        "专业组代码": "",
                        "院校专业组名称": "",
                        "专业组计划数": plan,
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "issues": ["院校专业组结构未完整识别"],
                        "_group_side": side,
                        "_group_page": page.page_number,
                        "_group_y1": min(token.y1 for token in row),
                        "_group_y2": max(token.y2 for token in row),
                    }
                    group_had_major = False
                    continue

                if not plan and score:
                    trailing_plan = re.fullmatch(r"(.+?)\s+(\d{1,3})", description)
                    if trailing_plan:
                        description = trailing_plan.group(1).strip()
                        plan = trailing_plan.group(2)
                major_match = major_pattern.fullmatch(description)
                if major_match and (plan or score):
                    finalize_major()
                    normalize_current_group()
                    issues = []
                    if current_group is None:
                        issues.append("专业行之前未识别到所属院校专业组")
                    if critical_score and numeric_confidence < critical_score:
                        issues.append("计划数或分数线 OCR 置信度较低")
                    current_major = {
                        "专业代码": major_match.group("major_code"),
                        "专业描述": major_match.group("major_name").strip(),
                        "科类": current_course,
                        "再选科目": current_subject,
                        "计划数": plan,
                        "分数线": score,
                        "来源文件": page.source_relative_path,
                        "来源页": str(page.page_number),
                        "来源行": str(visual_row_number),
                        "提取方式": page.method,
                        "原始行文本": joined_row,
                        "issues": issues,
                        "_major_side": side,
                        "_major_page": page.page_number,
                        "_major_y1": min(token.y1 for token in row),
                        "_major_y2": max(token.y2 for token in row),
                    }
                    continue

                if current_major is not None:
                    current_major["专业描述"] += description
                    current_major["原始行文本"] += " | " + joined_row
                elif current_group is not None:
                    current_group["院校名称"] += description
                    current_group["原始行文本"] += " | " + joined_row
                    normalize_current_group()

    finalize_major()
    add_orphan_group_issue()

    grouped: dict[tuple[str, str], list[ParsedRecord]] = {}
    for record in records:
        key = (
            str(record.values.get("来源文件", "")),
            str(record.values.get("院校专业组代码", "")),
        )
        grouped.setdefault(key, []).append(record)
    for group_records in grouped.values():
        expected = str(group_records[0].values.get("专业组计划数", "")).strip()
        plans = [str(record.values.get("计划数", "")).strip() for record in group_records]
        if expected.isdigit() and plans and all(plan.isdigit() for plan in plans):
            actual = sum(int(plan) for plan in plans)
            if actual != int(expected):
                issue = f"专业计划数合计 {actual} 与专业组计划数 {expected} 不一致"
                for record in group_records:
                    if issue not in record.issues:
                        record.issues.append(issue)
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


def _table_cell_lines(value: object) -> list[str]:
    text = str(value or "").replace("\r", "\n")
    return [
        _clean_table_cell(part)
        for part in re.split(r"\n+", text)
        if _clean_table_cell(part)
    ]


def _normalize_header(value: object) -> str:
    return re.sub(r"[\s（）()：:、，,。·/\\_\-]+", "", str(value or "")).casefold()


def _dynamic_column_map(
    row: list[object], profile: dict[str, Any]
) -> dict[str, int]:
    aliases = profile.get("_header_aliases") or {}
    allowed_fields = [
        str(field)
        for field in profile.get("header_fields", aliases.keys())
    ]
    normalized_cells = [_normalize_header(value) for value in row]
    result: dict[str, int] = {}
    used_columns: dict[int, str] = {}
    for field in allowed_fields:
        candidates = {
            _normalize_header(alias)
            for alias in [field, *(aliases.get(field) or [])]
            if str(alias).strip()
        }
        matches = [
            index for index, value in enumerate(normalized_cells)
            if value and value in candidates
        ]
        if len(matches) > 1:
            raise RuntimeError(f"表头字段“{field}”同时命中多列：{matches}")
        if not matches:
            continue
        column = matches[0]
        if column in used_columns:
            raise RuntimeError(
                f"表头列{column}同时映射为“{used_columns[column]}”和“{field}”，已停止解析。"
            )
        result[field] = column
        used_columns[column] = field
    required_headers = [str(field) for field in profile.get("required_header_fields", [])]
    if required_headers and not all(field in result for field in required_headers):
        return {}
    return result


def _split_structured_values(
    values: dict[str, Any],
    value_lines: dict[str, list[str]],
    profile: dict[str, Any],
) -> list[tuple[dict[str, Any], list[str]]]:
    rule = profile.get("split_merged_rows") or {}
    if not rule:
        return [(values, [])]
    anchor_field = str(rule.get("anchor_field", "专业代码"))
    anchor_lines = value_lines.get(anchor_field, [])
    anchor_pattern = re.compile(str(rule.get("anchor_pattern", r"[0-9A-Za-z]{2,4}")))
    if len(anchor_lines) <= 1 or not all(anchor_pattern.fullmatch(line) for line in anchor_lines):
        return [(values, [])]

    count = len(anchor_lines)
    split_fields = [str(field) for field in rule.get("fields", [])]
    repeat_fields = {str(field) for field in rule.get("repeat_fields", [])}
    incompatible = [
        field
        for field in split_fields
        if value_lines.get(field)
        and len(value_lines[field]) not in ({1, count} if field in repeat_fields else {count})
    ]
    if incompatible:
        return [(
            values,
            [
                "疑似同一单元格包含多条专业，但以下字段行数无法安全对齐："
                + "、".join(incompatible)
            ],
        )]

    rows: list[tuple[dict[str, Any], list[str]]] = []
    for index in range(count):
        copied = dict(values)
        for field in split_fields:
            lines = value_lines.get(field, [])
            if len(lines) == count:
                copied[field] = lines[index]
            elif len(lines) == 1 and field in repeat_fields:
                copied[field] = lines[0]
        rows.append((copied, [f"源表合并单元格已安全拆分为{count}条专业记录"]))
    return rows


def _apply_conditional_checks(
    values: dict[str, Any], profile: dict[str, Any]
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    for rule in profile.get("conditional_required", []):
        field = str(rule["field"])
        if not re.fullmatch(str(rule["pattern"]), str(values.get(field, "")).strip()):
            continue
        missing = [
            str(required)
            for required in rule.get("required", [])
            if not str(values.get(str(required), "")).strip()
        ]
        if missing:
            issues.append(
                str(rule.get("message") or "条件必填字段缺失：") + "、".join(missing)
            )
    for rule in profile.get("conditional_warnings", []):
        field = str(rule["field"])
        if re.fullmatch(str(rule["pattern"]), str(values.get(field, "")).strip()):
            warnings.append(str(rule["message"]))
    return issues, warnings


def _looks_like_structured_data_row(
    values: dict[str, Any],
    *,
    required: list[str],
    integer_fields: list[str],
    code_patterns: dict[str, re.Pattern[str]],
    minimum_populated: int,
) -> bool:
    """Keep damaged data rows without mistaking ordinary title/header rows for data."""
    populated = sum(bool(str(values.get(field, "")).strip()) for field in required)
    if populated < minimum_populated:
        return False
    has_numeric_evidence = any(
        str(values.get(field, "")).strip().isdigit() for field in integer_fields
    )
    has_code_evidence = any(
        bool(pattern.fullmatch(str(values.get(field, "")).strip()))
        for field, pattern in code_patterns.items()
    )
    return has_numeric_evidence or has_code_evidence


def parse_structured_table(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
    diagnostics: ParseDiagnostics | None = None,
) -> list[ParsedRecord]:
    """Parse native PDF/worksheet tables, preferring conservative header mapping."""
    if not any(page.tables for page in pages):
        fallback = profile.get("fallback_parser")
        if fallback == "spatial_rows":
            return parse_spatial_rows(
                pages,
                profile,
                y_tolerance_ratio=y_tolerance_ratio,
                diagnostics=diagnostics,
            )
        if fallback == "flat_major_plan":
            return parse_flat_major_plan(pages, profile, y_tolerance_ratio=y_tolerance_ratio)
        return []

    context = _extract_document_context(pages, profile)
    fallback_column_map = {
        str(field): int(index)
        for field, index in (profile.get("column_map") or {}).items()
    }
    required = [str(value) for value in profile.get("required", [])]
    integer_fields = [str(value) for value in profile.get("integer_fields", [])]
    allowed_values = {
        str(field): {str(value) for value in values}
        for field, values in (profile.get("allowed_values") or {}).items()
    }
    watermark_noise_chars = {
        str(value) for value in profile.get("watermark_noise_chars", [])
    }
    watermark_line_fields = {
        str(value) for value in profile.get("watermark_line_fields", [])
    }
    code_patterns = {
        str(field): re.compile(str(pattern))
        for field, pattern in (profile.get("code_patterns") or {}).items()
    }
    skip_markers = [str(value) for value in profile.get("skip_if_contains", [])]
    row_key = profile.get("row_key") or {}
    row_key_field = str(row_key.get("field", ""))
    row_key_pattern = re.compile(str(row_key.get("pattern", r".+")))
    section_pattern = (
        re.compile(str(profile["section_pattern"]))
        if profile.get("section_pattern")
        else None
    )
    section_field = str(profile.get("section_field", "计划类别"))
    current_section = ""
    records: list[ParsedRecord] = []
    detected_units = 0
    recovered_units = 0
    source_row = 0

    for page in pages:
        for table in page.tables:
            column_map = dict(fallback_column_map)
            for row in table:
                source_row += 1
                cells = [_clean_table_cell(value) for value in row]
                joined = "|".join(cells)
                if not any(cells) or any(marker in joined for marker in skip_markers):
                    continue
                if profile.get("dynamic_header_mapping", False):
                    detected = _dynamic_column_map(row, profile)
                    if detected:
                        column_map = detected
                        continue
                if not column_map:
                    continue
                nonempty = [value for value in cells if value]
                if section_pattern and len(nonempty) == 1 and section_pattern.fullmatch(nonempty[0]):
                    current_section = nonempty[0]
                    continue
                values: dict[str, Any] = dict(context)
                value_lines: dict[str, list[str]] = {}
                for field, index in column_map.items():
                    lines = _table_cell_lines(row[index]) if index < len(row) else []
                    if (
                        field in watermark_line_fields
                        and len(lines) > 1
                        and len(lines[0]) == 1
                        and lines[0] in watermark_noise_chars
                    ):
                        lines = lines[1:]
                    values[field] = "".join(lines) if lines else (
                        cells[index] if index < len(cells) else ""
                    )
                    value_lines[field] = lines
                for selector_field, choices in (profile.get("dynamic_fields") or {}).items():
                    selected = (choices or {}).get(str(values.get(selector_field, "")), {})
                    for source_field, target_field in selected.items():
                        values[str(target_field)] = values.pop(str(source_field), "")
                if current_section:
                    values[section_field] = current_section
                split_rows = _split_structured_values(values, value_lines, profile)
                for split_values, split_warnings in split_rows:
                    row_key_value = str(split_values.get(row_key_field, "")).strip()
                    row_key_valid = bool(row_key_pattern.fullmatch(row_key_value))
                    if not row_key_valid and not _looks_like_structured_data_row(
                        split_values,
                        required=required,
                        integer_fields=integer_fields,
                        code_patterns=code_patterns,
                        minimum_populated=int(
                            profile.get("candidate_row_minimum_populated", 2)
                        ),
                    ):
                        continue
                    detected_units += 1
                    if not row_key_valid:
                        recovered_units += 1
                    for rule in profile.get("derive_fields", []):
                        source_value = str(split_values.get(str(rule["source"]), ""))
                        match = re.search(str(rule["pattern"]), source_value)
                        if match:
                            for index, field in enumerate(rule.get("fields", []), start=1):
                                split_values[str(field)] = match.group(index)
                    for field in integer_fields:
                        split_values[field] = _strip_cjk_noise_from_integer_or_allowed(
                            str(split_values.get(field, "")),
                            allowed_values.get(field, set()),
                            watermark_noise_chars,
                        )
                    for field in profile.get("code_fields", []):
                        split_values[str(field)] = _strip_cjk_noise_from_typed_value(
                            str(split_values.get(str(field), "")), "code"
                        )
                    split_values.update(
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
                    if not row_key_valid:
                        issues.append(
                            f"检测到结构化数据行，但行关键字段异常：{row_key_field}（{row_key_value or '空'}）"
                        )
                    for field in required:
                        if not str(split_values.get(field, "")).strip():
                            issues.append(f"缺少必填字段：{field}")
                    for field in integer_fields:
                        value = str(split_values.get(field, "")).strip()
                        if (
                            value
                            and not value.isdigit()
                            and value not in allowed_values.get(field, set())
                        ):
                            issues.append(f"{field}：应为整数（{value}）")
                    for field, pattern in code_patterns.items():
                        value = str(split_values.get(field, "")).strip()
                        if value and not pattern.fullmatch(value):
                            issues.append(f"{field}：代码格式异常（{value}）")
                    field_evidence: dict[str, dict[str, Any]] = {}
                    for field, pattern in code_patterns.items():
                        value = str(split_values.get(field, "")).strip()
                        field_evidence[field] = {
                            "raw_value": value,
                            "final_value": value,
                            "status": (
                                "structured_source"
                                if value and pattern.fullmatch(value)
                                else "unresolved"
                                if value or field in required
                                else "absent_optional"
                            ),
                        }
                    for field in integer_fields:
                        value = str(split_values.get(field, "")).strip()
                        valid = value.isdigit() or value in allowed_values.get(field, set())
                        field_evidence.setdefault(
                            field,
                            {
                                "raw_value": value,
                                "final_value": value,
                                "status": (
                                    "structured_source"
                                    if value and valid
                                    else "unresolved"
                                    if value or field in required
                                    else "absent_optional"
                                ),
                            },
                        )
                    split_values["_field_evidence"] = field_evidence
                    conditional_issues, conditional_warnings = _apply_conditional_checks(
                        split_values, profile
                    )
                    records.append(
                        ParsedRecord(
                            values=split_values,
                            issues=[*issues, *conditional_issues],
                            warnings=[*split_warnings, *conditional_warnings],
                        )
                    )
    if diagnostics is not None:
        diagnostics.coverage_observable = True
        diagnostics.source_units_detected = detected_units
        diagnostics.records_emitted = len(records)
        diagnostics.recovered_units = recovered_units
        diagnostics.unparsed_units = detected_units - len(records)
    return records


def recheck_plan_totals(records: list[ParsedRecord]) -> None:
    mismatch_prefix = "专业计划数合计 "
    for record in records:
        record.issues = [
            issue for issue in record.issues if not issue.startswith(mismatch_prefix)
        ]

    grouped: dict[tuple[str, str, str, str], list[ParsedRecord]] = {}
    ungrouped: dict[tuple[str, str], list[ParsedRecord]] = {}
    for record in records:
        source = str(record.values.get("来源文件", ""))
        group_code = str(record.values.get("院校专业组代码", "")).strip()
        if group_code:
            key = (
                source,
                str(record.values.get("科类", "")).strip(),
                str(
                    record.values.get("再选科目", "")
                    or record.values.get("科目要求", "")
                ).strip(),
                group_code,
            )
            grouped.setdefault(key, []).append(record)
            continue
        school_code = str(record.values.get("院校代码", "")).strip()
        if school_code:
            ungrouped.setdefault((source, school_code), []).append(record)

    for group_records in grouped.values():
        expected = next(
            (
                str(record.values.get("专业组计划数", "")).strip()
                for record in group_records
                if str(record.values.get("专业组计划数", "")).strip()
            ),
            "",
        )
        plans = [str(record.values.get("计划数", "")).strip() for record in group_records]
        if expected.isdigit() and plans and all(plan.isdigit() for plan in plans):
            actual = sum(int(plan) for plan in plans)
            if actual != int(expected):
                issue = f"专业计划数合计 {actual} 与专业组计划数 {expected} 不一致"
                for record in group_records:
                    record.issues.append(issue)

    for school_records in ungrouped.values():
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
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    y_tolerance_ratio: float,
    source_path: Any = None,
    cell_ocr_cache_root: Any = None,
    diagnostics: ParseDiagnostics | None = None,
) -> list[ParsedRecord]:
    parser_name = profile.get("parser")
    if parser_name == "spatial_rows":
        return parse_spatial_rows(
            pages,
            profile,
            y_tolerance_ratio=y_tolerance_ratio,
            diagnostics=diagnostics,
        )
    if parser_name == "two_column_hierarchical_plan":
        return parse_two_column_hierarchical_plan(
            pages,
            profile,
            y_tolerance_ratio=y_tolerance_ratio,
            diagnostics=diagnostics,
        )
    if parser_name == "two_column_plan_balance_score":
        return parse_two_column_plan_balance_score(
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
            pages,
            profile,
            y_tolerance_ratio=y_tolerance_ratio,
            diagnostics=diagnostics,
        )
    if parser_name == "ruled_grid_table":
        from .grid_parsing import parse_ruled_grid_table

        return parse_ruled_grid_table(
            pages,
            profile,
            source_path=source_path,
            context=_extract_document_context(pages, profile),
            cell_ocr_cache_root=cell_ocr_cache_root,
            diagnostics=diagnostics,
        )
    raise ValueError(f"不支持的解析器：{parser_name}")
