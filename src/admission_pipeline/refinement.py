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
            for field_name, field_config in fields.items():
                current_value = str(record.values.get(field_name, "")).strip()
                pattern = re.compile(str(field_config["pattern"]))
                accept_if_source_invalid = bool(
                    field_config.get("accept_if_source_invalid", False)
                )
                if current_value and (
                    not accept_if_source_invalid or pattern.fullmatch(current_value)
                ):
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
                if score >= minimum_score and pattern.fullmatch(candidate):
                    if accept_if_source_invalid:
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
                    else:
                        candidates = record.values.setdefault("_ocr_candidates", {})
                        candidates[field_name] = candidate
        _approve_consistent_plan_refinements(records)
        for record in records:
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
            cache_path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return records


def _approve_consistent_plan_refinements(records: list[ParsedRecord]) -> None:
    grouped: dict[tuple[str, str], list[ParsedRecord]] = {}
    for record in records:
        group_code = str(record.values.get("院校专业组代码", "")).strip()
        key = (
            str(record.values.get("来源文件", "")),
            group_code or str(record.values.get("院校代码", "")),
        )
        grouped.setdefault(key, []).append(record)

    for school_records in grouped.values():
        uses_group_total = bool(
            str(school_records[0].values.get("院校专业组代码", "")).strip()
        )
        total_field = "专业组计划数" if uses_group_total else "院校计划总数"
        first_candidates = dict(school_records[0].values.get("_ocr_candidates", {}))
        expected = str(
            school_records[0].values.get(total_field, "")
            or first_candidates.get(total_field, "")
        ).strip()
        plans = []
        for record in school_records:
            candidates = dict(record.values.get("_ocr_candidates", {}))
            plans.append(
                str(record.values.get("计划数", "") or candidates.get("计划数", "")).strip()
            )
        if not expected.isdigit() or not plans or not all(plan.isdigit() for plan in plans):
            continue
        if sum(int(plan) for plan in plans) != int(expected):
            continue
        refined_sets = [set(dict(record.values.get("_ocr_candidates", {}))) for record in school_records]
        total_refined = any(total_field in fields for fields in refined_sets)
        plan_refined = any("计划数" in fields for fields in refined_sets)
        if plan_refined and not total_refined:
            for record, fields in zip(school_records, refined_sets, strict=True):
                if "计划数" in fields:
                    candidate = dict(record.values.get("_ocr_candidates", {}))["计划数"]
                    record.values["计划数"] = candidate
                    record.issues = [
                        issue
                        for issue in record.issues
                        if issue != "缺少必填字段：计划数"
                    ]
                    record.values["OCR补识别字段"] = "计划数"
                    record.values["OCR一致性校验"] = "专业计划合计与原始院校总数一致"
        elif total_refined and not plan_refined:
            for record, fields in zip(school_records, refined_sets, strict=True):
                if total_field in fields:
                    candidate = dict(record.values.get("_ocr_candidates", {}))[total_field]
                    record.values[total_field] = candidate
                    record.issues = [
                        issue
                        for issue in record.issues
                        if issue != f"缺少必填字段：{total_field}"
                    ]
                    record.values["OCR补识别字段"] = total_field
                    record.values["OCR一致性校验"] = (
                        "原始专业计划合计与专业组总数一致"
                        if uses_group_total
                        else "原始专业计划合计与院校总数一致"
                    )


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
    crop = ImageOps.autocontrast(ImageOps.grayscale(image.crop((x1, y1, x2, y2))))
    crop = ImageOps.expand(crop, border=4, fill=255).convert("RGB")
    enlarged = crop.resize(
        (crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS
    )
    result = ocr(
        np.asarray(enlarged),
        use_det=False,
        use_cls=False,
        use_rec=True,
    )
    texts = tuple(result.txts or ())
    scores = tuple(result.scores or ())
    if not texts or not scores:
        return {"text": "", "score": 0.0}
    return {"text": str(texts[0]), "score": float(scores[0])}


def _cache_path(
    config: ProjectConfig, item: InputFile, profile: dict[str, Any]
) -> Path:
    identity = f"{item.sha256}|{profile['name']}|cell-ocr-v2"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return config.path("temp_root") / "cache" / "cell_ocr" / f"{key}.json"


def _load_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
