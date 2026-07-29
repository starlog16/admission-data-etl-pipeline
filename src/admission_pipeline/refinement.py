from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from PIL import Image, ImageOps
from rapidocr import RapidOCR

from .config import ProjectConfig
from .domain import ExtractedPage, InputFile, ParsedRecord


def _update_field_evidence(
    record: ParsedRecord,
    field: str,
    *,
    final_value: str,
    status: str,
    cell_value: str = "",
    cell_confidence: float | None = None,
) -> None:
    evidence = record.values.setdefault("_field_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
        record.values["_field_evidence"] = evidence
    details = evidence.setdefault(
        field,
        {"raw_value": str(record.values.get(field, "")), "final_value": final_value},
    )
    if not isinstance(details, dict):
        details = {}
        evidence[field] = details
    details.update({"final_value": final_value, "status": status})
    if cell_value:
        details["cell_value"] = cell_value
    if cell_confidence is not None:
        details["cell_confidence"] = round(cell_confidence, 5)


def refine_missing_numeric_cells(
    config: ProjectConfig,
    item: InputFile,
    pages: list[ExtractedPage],
    records: list[ParsedRecord],
    profile: dict[str, Any],
    ocr: RapidOCR,
) -> list[ParsedRecord]:
    settings = profile.get("cell_ocr_refinement") or {}
    if not settings.get("enabled", False) or not records:
        return records

    page_lookup = {page.page_number: page for page in pages}
    regions = profile["page_regions"]
    scale = int(settings.get("scale", 8))
    minimum_score = float(settings.get("minimum_score", 0.90))
    fields = settings.get("fields", {})
    cache_path = _cache_path(config, item, profile)
    cache = _load_cache(cache_path)
    changed_cache = False
    image_cache: dict[int, Image.Image] = {}
    pdf_document: pymupdf.Document | None = None

    try:
        if item.kind == "pdf":
            pdf_document = pymupdf.open(item.path)

        def page_image(page_number: int) -> Image.Image:
            if page_number in image_cache:
                return image_cache[page_number]
            if item.kind == "image":
                image = Image.open(item.path).convert("RGB")
            else:
                assert pdf_document is not None
                dpi = int(config.extraction["pdf_render_dpi"])
                ratio = dpi / 72.0
                pixmap = pdf_document[page_number - 1].get_pixmap(
                    matrix=pymupdf.Matrix(ratio, ratio), alpha=False
                )
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
            image_cache[page_number] = image
            return image

        for record in records:
            pair_values: dict[str, str] = {}
            pair_settings = settings.get("numeric_pair_ocr") or {}
            pair_issue = (
                any(issue.startswith("专业计划数合计 ") for issue in record.issues)
                or "计划数或分数线 OCR 置信度较低" in record.issues
                or any(
                    issue in {"缺少必填字段：计划数", "缺少必填字段：分数线"}
                    for issue in record.issues
                )
            )
            if pair_settings.get("enabled", False) and pair_issue:
                side = str(record.values.get("_major_side", ""))
                page_number_raw = record.values.get("_major_page", "")
                y1_raw = record.values.get("_major_y1", "")
                y2_raw = record.values.get("_major_y2", "")
                if side and page_number_raw != "" and y1_raw != "" and y2_raw != "":
                    page_number = int(page_number_raw)
                    extracted_page = page_lookup.get(page_number)
                    if extracted_page is not None:
                        key = (
                            f"{page_number}|{side}|major|{float(y1_raw):.2f}|"
                            f"{float(y2_raw):.2f}|numeric_pair"
                        )
                        if key not in cache:
                            cache[key] = _recognize_cell(
                                page_image(page_number),
                                extracted_page,
                                float(y1_raw),
                                float(y2_raw),
                                regions[side][str(pair_settings.get("band", "numeric"))],
                                ocr,
                                scale=int(pair_settings.get("scale", scale)),
                            )
                            changed_cache = True
                        pair_result = cache[key]
                        if (
                            int(pair_result.get("votes", 1)) >= 2
                            and float(pair_result.get("score", 0.0)) >= float(
                                pair_settings.get("minimum_score", minimum_score)
                            )
                        ):
                            pair_values = _parse_numeric_pair(
                                str(pair_result.get("text", ""))
                            )

            for field_name, field_config in fields.items():
                current_value = str(record.values.get(field_name, "")).strip()
                pattern = re.compile(str(field_config["pattern"]))
                accept_if_source_invalid = bool(
                    field_config.get("accept_if_source_invalid", False)
                )
                current_is_valid = bool(current_value and pattern.fullmatch(current_value))
                plan_mismatch = any(
                    issue.startswith("专业计划数合计 ")
                    for issue in record.issues
                )
                low_numeric_confidence = "计划数或分数线 OCR 置信度较低" in record.issues
                recheck_existing = (
                    field_name in {"专业组计划数", "计划数"}
                    and plan_mismatch
                ) or (
                    field_name in {"计划数", "分数线"}
                    and low_numeric_confidence
                )
                if current_is_valid and not recheck_existing:
                    continue
                row_kind = str(field_config["row"])
                side = str(record.values.get(f"_{row_kind}_side", ""))
                page_number_raw = record.values.get(f"_{row_kind}_page", "")
                y1_raw = record.values.get(f"_{row_kind}_y1", "")
                y2_raw = record.values.get(f"_{row_kind}_y2", "")
                if not side or page_number_raw == "" or y1_raw == "" or y2_raw == "":
                    continue
                page_number = int(page_number_raw)
                extracted_page = page_lookup.get(page_number)
                if extracted_page is None:
                    continue
                key = (
                    f"{page_number}|{side}|{row_kind}|{float(y1_raw):.2f}|"
                    f"{float(y2_raw):.2f}|{field_name}"
                )
                if key not in cache:
                    image = page_image(page_number)
                    band_name = str(field_config["band"])
                    result = _recognize_cell(
                        image,
                        extracted_page,
                        float(y1_raw),
                        float(y2_raw),
                        regions[side][band_name],
                        ocr,
                        scale=scale,
                    )
                    cache[key] = result
                    changed_cache = True
                candidate = str(cache[key].get("text", "")).strip().replace(" ", "")
                score = float(cache[key].get("score", 0.0))
                votes = int(cache[key].get("votes", 1))
                recognized = []
                if votes >= 2 and score >= minimum_score and pattern.fullmatch(candidate):
                    recognized.append(candidate)
                pair_candidate = pair_values.get(field_name, "")
                if pair_candidate and pattern.fullmatch(pair_candidate):
                    recognized.append(pair_candidate)
                recognized = list(dict.fromkeys(recognized))
                if len(recognized) == 1:
                    candidate = recognized[0]
                    if candidate == current_value:
                        verified = set(record.values.get("_ocr_verified_fields", []))
                        verified.add(field_name)
                        record.values["_ocr_verified_fields"] = sorted(verified)
                        _update_field_evidence(
                            record,
                            field_name,
                            final_value=current_value,
                            status="cell_verified",
                            cell_value=candidate,
                            cell_confidence=score,
                        )
                    elif accept_if_source_invalid and not current_is_valid:
                        record.values[field_name] = candidate
                        record.issues = [
                            issue
                            for issue in record.issues
                            if issue != f"缺少必填字段：{field_name}"
                            and not issue.startswith(f"{field_name}：")
                            and not (
                                field_name == "分数线"
                                and str(record.values.get("计划数", "")).strip()
                                and issue == "计划数或分数线 OCR 置信度较低"
                            )
                        ]
                        old_fields = str(record.values.get("OCR补识别字段", "")).strip()
                        record.values["OCR补识别字段"] = "、".join(
                            dict.fromkeys(filter(None, [old_fields, field_name]))
                        )
                        record.values["OCR一致性校验"] = (
                            "原值缺失或超范围，已用局部高置信度 OCR 复核"
                        )
                        verified = set(record.values.get("_ocr_verified_fields", []))
                        verified.add(field_name)
                        record.values["_ocr_verified_fields"] = sorted(verified)
                        _update_field_evidence(
                            record,
                            field_name,
                            final_value=candidate,
                            status="cell_corrected",
                            cell_value=candidate,
                            cell_confidence=score,
                        )
                    else:
                        candidates = record.values.setdefault("_ocr_candidates", {})
                        candidates[field_name] = candidate
                elif _is_consensus_arithmetic_candidate(
                    field_name, candidate, pattern, votes
                ):
                    # A low recognition score can be caused by a watermark even
                    # when all image variants agree.  Keep it only as a candidate;
                    # it may be promoted later if the independent printed total
                    # makes the complete set of plans balance exactly.
                    candidates = record.values.setdefault("_ocr_candidates", {})
                    candidates[field_name] = candidate
        _approve_consistent_plan_refinements(records)
        for record in records:
            verified = set(record.values.get("_ocr_verified_fields", []))
            if {"计划数", "分数线"}.issubset(verified):
                record.issues = [
                    issue
                    for issue in record.issues
                    if issue != "计划数或分数线 OCR 置信度较低"
                ]
            candidates = dict(record.values.get("_ocr_candidates", {}))
            remaining = [
                f"{field}={value}"
                for field, value in candidates.items()
                if str(record.values.get(field, "")).strip() != str(value)
            ]
            if remaining:
                record.values["OCR候选值（未通过交叉校验）"] = "；".join(remaining)
    finally:
        if pdf_document is not None:
            pdf_document.close()
        for image in image_cache.values():
            image.close()
        if changed_cache:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(cache_path)
    return records


def _parse_numeric_pair(value: str) -> dict[str, str]:
    digits = "".join(re.findall(r"\d", value))
    if len(digits) == 3 and 100 <= int(digits) <= 750:
        return {"分数线": digits}
    if not 4 <= len(digits) <= 6:
        return {}
    plan, score = digits[:-3], digits[-3:]
    if not plan.isdigit() or not score.isdigit():
        return {}
    if not 1 <= int(plan) <= 999 or not 100 <= int(score) <= 750:
        return {}
    return {"计划数": plan, "分数线": score}


def _is_consensus_arithmetic_candidate(
    field_name: str,
    candidate: str,
    pattern: re.Pattern[str],
    votes: int,
) -> bool:
    return (
        field_name in {"院校计划总数", "专业组计划数", "计划数"}
        and votes >= 2
        and bool(pattern.fullmatch(candidate))
    )


def _approve_consistent_plan_refinements(records: list[ParsedRecord]) -> None:
    grouped: dict[tuple[str, ...], list[ParsedRecord]] = {}
    for record in records:
        group_code = str(record.values.get("院校专业组代码", "")).strip()
        source = str(record.values.get("来源文件", ""))
        if group_code:
            key = (
                "group",
                source,
                str(record.values.get("科类", "")).strip(),
                str(
                    record.values.get("再选科目", "")
                    or record.values.get("科目要求", "")
                ).strip(),
                group_code,
            )
        else:
            # A school-level printed total covers every major under that school,
            # including majors with different subject requirements.
            key = ("school", source, str(record.values.get("院校代码", "")))
        grouped.setdefault(key, []).append(record)

    for school_records in grouped.values():
        uses_group_total = bool(
            str(school_records[0].values.get("院校专业组代码", "")).strip()
        )
        total_field = "专业组计划数" if uses_group_total else "院校计划总数"
        first_candidates = dict(school_records[0].values.get("_ocr_candidates", {}))
        original_expected = str(school_records[0].values.get(total_field, "")).strip()
        candidate_expected = str(first_candidates.get(total_field, "")).strip()
        original_plans = []
        candidate_plans = []
        for record in school_records:
            candidates = dict(record.values.get("_ocr_candidates", {}))
            original = str(record.values.get("计划数", "")).strip()
            original_plans.append(original)
            candidate_plans.append(str(candidates.get("计划数", original)).strip())

        plan_candidates_present = any(
            "计划数" in dict(record.values.get("_ocr_candidates", {}))
            for record in school_records
        )
        # Safety rule: a second-pass value is promoted only when the other side
        # of the arithmetic check came from the original extraction. Two OCR
        # candidates are never allowed to validate each other.
        promote_plans = (
            plan_candidates_present
            and original_expected.isdigit()
            and candidate_plans
            and all(plan.isdigit() for plan in candidate_plans)
            and sum(int(plan) for plan in candidate_plans) == int(original_expected)
        )
        promote_total = (
            bool(candidate_expected)
            and candidate_expected.isdigit()
            and original_plans
            and all(plan.isdigit() for plan in original_plans)
            and sum(int(plan) for plan in original_plans) == int(candidate_expected)
        )

        if promote_plans:
            for record in school_records:
                candidates = dict(record.values.get("_ocr_candidates", {}))
                if "计划数" not in candidates:
                    continue
                candidate = str(candidates["计划数"])
                if candidate == str(record.values.get("计划数", "")).strip():
                    continue
                record.values["计划数"] = candidate
                record.issues = [
                    issue
                    for issue in record.issues
                    if issue != "缺少必填字段：计划数"
                ]
                old_fields = str(record.values.get("OCR补识别字段", "")).strip()
                record.values["OCR补识别字段"] = "、".join(
                    dict.fromkeys(filter(None, [old_fields, "计划数"]))
                )
                record.values["OCR一致性校验"] = "局部 OCR 专业计划合计与原始专业组总数一致"
                verified = set(record.values.get("_ocr_verified_fields", []))
                verified.add("计划数")
                record.values["_ocr_verified_fields"] = sorted(verified)
                _update_field_evidence(
                    record,
                    "计划数",
                    final_value=candidate,
                    status="arithmetic_crosscheck_corrected",
                    cell_value=candidate,
                )
        elif promote_total:
            for record in school_records:
                record.values[total_field] = candidate_expected
                record.issues = [
                    issue
                    for issue in record.issues
                    if issue != f"缺少必填字段：{total_field}"
                ]
                old_fields = str(record.values.get("OCR补识别字段", "")).strip()
                record.values["OCR补识别字段"] = "、".join(
                    dict.fromkeys(filter(None, [old_fields, total_field]))
                )
                record.values["OCR一致性校验"] = (
                    "原始专业计划合计与局部 OCR 专业组总数一致"
                    if uses_group_total
                    else "原始专业计划合计与局部 OCR 院校总数一致"
                )
                _update_field_evidence(
                    record,
                    total_field,
                    final_value=candidate_expected,
                    status="arithmetic_crosscheck_corrected",
                    cell_value=candidate_expected,
                )

        current_expected = str(
            school_records[0].values.get(total_field, "")
        ).strip()
        unresolved = [
            record
            for record in school_records
            if not str(record.values.get("计划数", "")).strip().isdigit()
        ]
        if current_expected.isdigit() and len(unresolved) == 1:
            known_total = sum(
                int(str(record.values.get("计划数", "")).strip())
                for record in school_records
                if str(record.values.get("计划数", "")).strip().isdigit()
            )
            residual = int(current_expected) - known_total
            record = unresolved[0]
            candidate = str(
                dict(record.values.get("_ocr_candidates", {})).get("计划数", "")
            ).strip()
            if 1 <= residual <= 999 and candidate in {"", str(residual)}:
                recovered = str(residual)
                record.values["计划数"] = recovered
                record.issues = [
                    issue
                    for issue in record.issues
                    if issue != "缺少必填字段：计划数"
                    and not issue.startswith("计划数：")
                ]
                old_fields = str(record.values.get("OCR补识别字段", "")).strip()
                record.values["OCR补识别字段"] = "、".join(
                    dict.fromkeys(filter(None, [old_fields, "计划数"]))
                )
                note = (
                    "源记录完整性已确认，计划数由院校计划总数与其余专业计划数"
                    "的唯一差额推导"
                )
                record.values["OCR一致性校验"] = note
                record.warnings.append(note)
                _update_field_evidence(
                    record,
                    "计划数",
                    final_value=recovered,
                    status="arithmetic_residual_recovered",
                )


def apply_verified_record_corrections(
    records: list[ParsedRecord], profile: dict[str, Any]
) -> list[ParsedRecord]:
    """Apply narrowly-scoped, source-audited corrections declared by a profile."""
    for correction in profile.get("verified_record_corrections", []):
        scope_contains = {
            str(key): str(value)
            for key, value in correction.get("scope_contains", {}).items()
        }
        scoped_records = [
            record
            for record in records
            if all(
                value in str(record.values.get(key, ""))
                for key, value in scope_contains.items()
            )
        ]
        if scope_contains and not scoped_records:
            continue
        exact = {str(key): str(value) for key, value in correction.get("match", {}).items()}
        contains = {
            str(key): str(value) for key, value in correction.get("contains", {}).items()
        }
        matches = [
            record
            for record in scoped_records if scope_contains
        ] if not exact and not contains else [
            record
            for record in (scoped_records if scope_contains else records)
            if all(str(record.values.get(key, "")) == value for key, value in exact.items())
            and all(value in str(record.values.get(key, "")) for key, value in contains.items())
        ]
        expected_matches = int(correction.get("expected_matches", 1))
        if len(matches) != expected_matches:
            label = str(correction.get("label", "未命名校正"))
            raise RuntimeError(
                f"已核验校正“{label}”匹配 {len(matches)} 条，"
                f"期望 {expected_matches} 条；已停止导出，避免将校正应用到错误记录。"
            )
        for record in matches:
            for field, value in correction.get("set", {}).items():
                record.values[str(field)] = value
                _update_field_evidence(
                    record,
                    str(field),
                    final_value=str(value),
                    status="source_manually_verified",
                )
            remove_exact = {
                str(issue) for issue in correction.get("remove_issues", [])
            }
            remove_prefixes = tuple(
                str(prefix) for prefix in correction.get("remove_issue_prefixes", [])
            )
            record.issues = [
                issue
                for issue in record.issues
                if issue not in remove_exact
                and not (remove_prefixes and issue.startswith(remove_prefixes))
            ]
            note = str(correction.get("note", "已依照源文件人工核验校正"))
            old_note = str(record.values.get("OCR一致性校验", "")).strip()
            record.values["OCR一致性校验"] = "；".join(
                dict.fromkeys(filter(None, [old_note, note]))
            )
            record.warnings.append(note)
    return records


def _recognize_cell(
    image: Image.Image,
    page: ExtractedPage,
    row_y1: float,
    row_y2: float,
    x_band: list[float],
    ocr: RapidOCR,
    *,
    scale: int,
) -> dict[str, object]:
    x1 = max(0, int(float(x_band[0]) * image.width))
    x2 = min(image.width, int(float(x_band[1]) * image.width))
    y_ratio = image.height / page.height
    row_height = max(2.0, (row_y2 - row_y1) * y_ratio)
    padding = max(2, int(row_height * 0.25))
    y1 = max(0, int(row_y1 * y_ratio) - padding)
    y2 = min(image.height, int(row_y2 * y_ratio) + padding)
    if x2 <= x1 or y2 <= y1:
        return {"text": "", "score": 0.0}
    crop = image.crop((x1, y1, x2, y2)).convert("RGB")
    rgb = np.asarray(crop, dtype=np.uint8)
    spread = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    mean = rgb.mean(axis=2).astype(np.uint8)
    variants = (
        ImageOps.autocontrast(ImageOps.grayscale(crop)),
        ImageOps.autocontrast(Image.fromarray(rgb.max(axis=2))),
        ImageOps.autocontrast(
            Image.fromarray(np.where(spread <= 25, mean, 255).astype(np.uint8))
        ),
    )
    candidates: dict[str, list[float]] = {}
    for variant in variants:
        padded = ImageOps.expand(variant, border=4, fill=255).convert("RGB")
        enlarged = padded.resize(
            (padded.width * scale, padded.height * scale), Image.Resampling.LANCZOS
        )
        result = ocr(
            np.asarray(enlarged),
            use_det=False,
            use_cls=False,
            use_rec=True,
        )
        for text, score in zip(
            tuple(result.txts or ()), tuple(result.scores or ()), strict=False
        ):
            value = str(text).strip().replace(" ", "")
            if value:
                candidates.setdefault(value, []).append(float(score))
    consensus = [
        (value, min(scores), len(scores))
        for value, scores in candidates.items()
        if len(scores) >= 2
    ]
    if not consensus:
        return {"text": "", "score": 0.0, "votes": 0}
    value, score, votes = max(consensus, key=lambda item: (item[2], item[1]))
    return {"text": value, "score": score, "votes": votes}


def _cache_path(
    config: ProjectConfig, item: InputFile, profile: dict[str, Any]
) -> Path:
    identity = f"{item.sha256}|{profile['name']}|cell-ocr-v3-color-consensus"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return config.path("temp_root") / "cache" / "cell_ocr" / f"{key}.json"


def _load_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}
