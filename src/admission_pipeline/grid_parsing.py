from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pymupdf
from PIL import Image, ImageOps
from rapidocr import RapidOCR

from .domain import ExtractedPage, ParseDiagnostics, ParsedRecord, Token


@dataclass(frozen=True, slots=True)
class _GridRow:
    center_y: float
    top: float
    bottom: float
    boundary_method: str
    anchor: Token | None
    recovered: bool = False
    presence_count: int = 0


def _clean(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip()


def _normalize_code(value: str) -> str:
    return (
        _clean(value)
        .upper()
        .replace("Ｏ", "0")
        .replace("O", "0")
        .replace("Ｉ", "1")
    )


def _grouped_centers(values: Iterable[int]) -> list[float]:
    groups: list[list[int]] = []
    for value in sorted(values):
        if not groups or value > groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def _page_grayscale(page: pymupdf.Page, width: int, height: int) -> np.ndarray:
    sx = width / page.rect.width
    sy = height / page.rect.height
    pix = page.get_pixmap(matrix=pymupdf.Matrix(sx, sy), colorspace=pymupdf.csGRAY, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return image[:height, :width]


def _detect_horizontal_lines(page: pymupdf.Page, width: int, height: int) -> list[float]:
    image = _page_grayscale(page, width, height)
    x1, x2 = int(width * 0.11), int(width * 0.87)
    band = image[:, x1:x2]
    candidates = np.where((band < 175).sum(axis=1) >= int((x2 - x1) * 0.38))[0]
    return _grouped_centers(int(value) for value in candidates)


def _detect_vertical_lines(page: pymupdf.Page, width: int, height: int) -> list[float]:
    image = _page_grayscale(page, width, height)
    y1, y2 = int(height * 0.05), int(height * 0.88)
    dark_counts = (image[y1:y2, :] < 175).sum(axis=0)
    x_start, x_end = int(width * 0.08), int(width * 0.92)
    local_max = int(dark_counts[x_start:x_end].max())
    candidates = np.where(dark_counts >= max(20, int(local_max * 0.72)))[0]
    return [
        center
        for center in _grouped_centers(int(value) for value in candidates)
        if width * 0.08 <= center <= width * 0.92
    ]


def _row_bounds(
    anchor_y: float, horizontal_lines: list[float], neighbor_ys: list[float]
) -> tuple[float, float, str]:
    above = [line for line in horizontal_lines if line < anchor_y - 2]
    below = [line for line in horizontal_lines if line > anchor_y + 2]
    if above and below:
        top, bottom = max(above), min(below)
        if 16 <= bottom - top <= 105:
            return top + 1, bottom - 1, "grid"
    previous = [y for y in neighbor_ys if y < anchor_y and anchor_y - y < 100]
    following = [y for y in neighbor_ys if y > anchor_y and y - anchor_y < 100]
    top = (max(previous) + anchor_y) / 2 if previous else anchor_y - 22
    bottom = (min(following) + anchor_y) / 2 if following else anchor_y + 22
    return top, bottom, "estimated"


def _find_grid_rows(
    page: ExtractedPage,
    *,
    vertical: list[float],
    horizontal: list[float],
    anchor_column: int,
    anchor_pattern: re.Pattern[str],
    field_columns: dict[str, tuple[int, int]],
    recover_unanchored: bool,
    row_presence_fields: list[str],
    row_presence_min_fields: int,
    row_header_terms: list[str],
    recovered_row_min_height: float = 16.0,
    recovered_row_max_height: float = 105.0,
) -> list[_GridRow]:
    """Find every data row, retaining ruled rows whose institution OCR is invalid."""
    anchors = sorted(
        [
            token
            for token in page.tokens
            if anchor_pattern.fullmatch(_normalize_code(token.text))
            and vertical[anchor_column] < token.cx < vertical[anchor_column + 1]
        ],
        key=lambda token: token.cy,
    )
    anchor_ys = [token.cy for token in anchors]
    rows = [
        _GridRow(
            center_y=anchor.cy,
            top=bounds[0],
            bottom=bounds[1],
            boundary_method=bounds[2],
            anchor=anchor,
            presence_count=len(row_presence_fields),
        )
        for anchor in anchors
        for bounds in [_row_bounds(anchor.cy, horizontal, anchor_ys)]
    ]
    if not recover_unanchored:
        return rows

    header_terms = [term for term in row_header_terms if term]
    for top_line, bottom_line in zip(horizontal, horizontal[1:], strict=False):
        height = bottom_line - top_line
        if not recovered_row_min_height <= height <= recovered_row_max_height:
            continue
        if any(top_line < anchor_y < bottom_line for anchor_y in anchor_ys):
            continue
        top, bottom = top_line + 1, bottom_line - 1
        field_values: dict[str, str] = {}
        for field in row_presence_fields:
            pair = field_columns.get(field)
            if pair is None:
                continue
            left, right = pair
            field_values[field] = _join_cell(
                _tokens_in_cell(
                    page.tokens,
                    vertical[left] + 1,
                    vertical[right] - 1,
                    top,
                    bottom,
                )
            ).strip()
        joined = "|".join(field_values.values())
        if any(term in joined for term in header_terms):
            continue
        presence_count = sum(bool(_clean(value)) for value in field_values.values())
        center_y = (top_line + bottom_line) / 2
        inside_anchor_span = bool(anchor_ys) and min(anchor_ys) <= center_y <= max(anchor_ys)
        adjacent_edge_guard = bool(anchor_ys) and min(
            abs(center_y - anchor_y) for anchor_y in anchor_ys
        ) <= height * 1.5
        if (
            presence_count < row_presence_min_fields
            and not inside_anchor_span
            and not adjacent_edge_guard
        ):
            continue
        rows.append(
            _GridRow(
                center_y=center_y,
                top=top,
                bottom=bottom,
                boundary_method="grid",
                anchor=None,
                recovered=True,
                presence_count=presence_count,
            )
        )
    return sorted(rows, key=lambda row: row.center_y)


def _tokens_in_cell(
    tokens: list[Token], x1: float, x2: float, y1: float, y2: float
) -> list[Token]:
    return sorted(
        [
            token
            for token in tokens
            if x1 <= token.cx < x2 and y1 <= token.cy <= y2 and _clean(token.text)
        ],
        key=lambda token: (round(token.cy / 5), token.x1),
    )


def _join_cell(tokens: list[Token]) -> str:
    if not tokens:
        return ""
    lines: list[list[Token]] = []
    for token in sorted(tokens, key=lambda item: (item.cy, item.x1)):
        if not lines or abs(
            token.cy - sum(item.cy for item in lines[-1]) / len(lines[-1])
        ) > 8:
            lines.append([token])
        else:
            lines[-1].append(token)
    return "".join(
        "".join(_clean(token.text) for token in sorted(line, key=lambda item: item.x1))
        for line in lines
    )


def _score_floor(tokens: list[Token]) -> float:
    return min((token.score for token in tokens), default=0.0)


def _normalize_subject(value: str) -> str:
    compact = _clean(value)
    if "不限" in compact:
        return "不限"
    found: list[str] = []
    for source, target in (
        ("化学", "化学"), ("生物", "生物"), ("思想政治", "思想政治"),
        ("政治", "思想政治"), ("地理", "地理"),
    ):
        if source in compact and target not in found:
            found.append(target)
    return "/".join(found)


def _separate_major_code_from_name(
    major_code: str, major_name: str, pattern: re.Pattern[str]
) -> tuple[str, str, str]:
    code = _normalize_code(major_code)
    name = _clean(major_name)
    if code and name.startswith(code) and name != code:
        return code, name[len(code):], "专业名称开头重复的专业代码已按列边界去除"
    prefixed = re.fullmatch(r"([0-9A-Z]{2,4})(.+)", name)
    if not pattern.fullmatch(code) and prefixed and pattern.fullmatch(prefixed.group(1)):
        return prefixed.group(1), prefixed.group(2), "专业代码从相邻专业名称单元格边界恢复"
    return code, name, ""


def _render_page(page: pymupdf.Page, width: int, height: int) -> Image.Image:
    sx = width / page.rect.width
    sy = height / page.rect.height
    pix = page.get_pixmap(matrix=pymupdf.Matrix(sx, sy), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _recognize_cell(
    image: Image.Image,
    x1: float,
    x2: float,
    y1: float,
    y2: float,
    ocr: RapidOCR,
    pattern: re.Pattern[str],
) -> tuple[str, float]:
    left, right = max(0, int(x1) + 2), min(image.width, int(x2) - 2)
    top, bottom = max(0, int(y1) + 1), min(image.height, int(y2) - 1)
    if right <= left or bottom <= top:
        return "", 0.0
    crop = image.crop((left, top, right, bottom)).convert("RGB")
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
    candidates: list[tuple[str, float]] = []
    for variant in variants:
        padded = ImageOps.expand(variant, border=8, fill=255).convert("RGB")
        enlarged = padded.resize(
            (padded.width * 8, padded.height * 8), Image.Resampling.LANCZOS
        )
        result = ocr(np.asarray(enlarged), use_det=False, use_cls=False, use_rec=True)
        for text, score in zip(
            tuple(result.txts or ()), tuple(result.scores or ()), strict=False
        ):
            value = _normalize_code(str(text))
            if pattern.fullmatch(value):
                candidates.append((value, float(score)))
    return _select_consensus_candidate(candidates)


class _GridCellOcrCache:
    VERSION = "grid-cell-v1"

    def __init__(
        self,
        root: Path | None,
        *,
        source_sha256: str,
        profile_name: str,
    ) -> None:
        self.path: Path | None = None
        self.entries: dict[str, tuple[str, float]] = {}
        self.dirty = False
        self.hits = 0
        self.misses = 0
        if root is None:
            return
        identity = f"{source_sha256}|{profile_name}|{self.VERSION}"
        self.path = root / f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()}.json"
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = {
                    str(key): (str(value[0]), float(value[1]))
                    for key, value in (payload.get("entries") or {}).items()
                }
            except (OSError, ValueError, TypeError, IndexError):
                # A broken optional cache must never corrupt data or stop parsing.
                self.entries = {}

    def recognize(
        self,
        *,
        page_number: int,
        field: str,
        image: Image.Image,
        x1: float,
        x2: float,
        y1: float,
        y2: float,
        ocr: RapidOCR,
        pattern: re.Pattern[str],
    ) -> tuple[str, float]:
        key = "|".join(
            [
                str(page_number),
                field,
                pattern.pattern,
                *(f"{value:.2f}" for value in (x1, x2, y1, y2)),
            ]
        )
        cached = self.entries.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        result = _recognize_cell(image, x1, x2, y1, y2, ocr, pattern)
        self.entries[key] = result
        self.dirty = True
        return result

    def save(self) -> None:
        if self.path is None or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": self.VERSION,
                    "entries": self.entries,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self.dirty = False


def _select_consensus_candidate(
    candidates: list[tuple[str, float]],
) -> tuple[str, float]:
    """Accept a cell code only when two independent image variants agree."""
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    for value, score in candidates:
        if value:
            grouped[value].append(float(score))
    consensus = [
        (value, min(scores), len(scores))
        for value, scores in grouped.items()
        if len(scores) >= 2
    ]
    if not consensus:
        return "", 0.0
    winner = max(consensus, key=lambda item: (item[2], item[1]))
    if sum(
        1
        for value, score, count in consensus
        if (count, score) == (winner[2], winner[1]) and value != winner[0]
    ):
        return "", 0.0
    return winner[0], winner[1]


def _reconcile_cell_code(
    *,
    field: str,
    current: str,
    current_score: float = 0.0,
    recognized: str,
    score: float,
    minimum_score: float,
    current_minimum_score: float | None = None,
    always_recheck: bool,
) -> tuple[str, list[str], list[str]]:
    """Prefer an isolated grid-cell consensus for critical codes, never a guess."""
    if recognized and recognized == current:
        return current, [], []
    if not recognized or score < minimum_score:
        source_threshold = (
            minimum_score if current_minimum_score is None else current_minimum_score
        )
        if not recognized and current and current_score >= source_threshold:
            return current, [], []
        if always_recheck:
            return current, [f"{field}网格单元格双路复识未达可信阈值"], []
        return current, [], []
    if always_recheck:
        return (
            recognized,
            [],
            [f"{field}按网格单元格双路复识校正：{current or '空'}→{recognized}"],
        )
    return current, [f"{field}整页OCR与单元格OCR不一致（{current}/{recognized}）"], []


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


def _semantic_reuse_key(
    field: str, values: dict[str, Any]
) -> tuple[str, str, str, str]:
    label_field = {
        "专业组代码": "专业组名称",
    }.get(field, "")
    return (
        str(values.get("来源文件", "")).strip(),
        str(values.get("科类", "")).strip(),
        field,
        str(values.get(label_field, "")).strip(),
    )


def _is_adjacent_transposition(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    different = [
        index
        for index, (left_char, right_char) in enumerate(
            zip(left, right, strict=True)
        )
        if left_char != right_char
    ]
    return (
        len(different) == 2
        and different[1] == different[0] + 1
        and left[different[0]] == right[different[1]]
        and left[different[1]] == right[different[0]]
    )


def _reconcile_repeated_values(records: list[ParsedRecord]) -> None:
    institution_pattern = re.compile(r"\d{4}")
    by_name: defaultdict[tuple[str, str, str], list[ParsedRecord]] = defaultdict(list)
    for record in records:
        name = str(record.values.get("院校名称", "")).strip()
        if name:
            key = (
                str(record.values.get("来源文件", "")).strip(),
                str(record.values.get("科类", "")).strip(),
                name,
            )
            by_name[key].append(record)
    for items in by_name.values():
        counts = Counter(
            str(item.values.get("院校代码", "")).strip()
            for item in items
            if institution_pattern.fullmatch(
                str(item.values.get("院校代码", "")).strip()
            )
        )
        if not counts:
            continue
        winner, count = counts.most_common(1)[0]
        if count < 2 or count / counts.total() < 0.75:
            continue
        for record in items:
            old = str(record.values.get("院校代码", "")).strip()
            one_character_difference = (
                len(old) == len(winner) == 4
                and sum(
                    left != right
                    for left, right in zip(old, winner, strict=True)
                )
                == 1
            )
            safely_recoverable = (
                not institution_pattern.fullmatch(old)
                or one_character_difference
                or _is_adjacent_transposition(old, winner)
            )
            if old != winner and safely_recoverable:
                record.values["院校代码"] = winner
                evidence = record.values.get("_field_evidence", {})
                if isinstance(evidence, dict) and isinstance(
                    evidence.get("院校代码"), dict
                ):
                    evidence["院校代码"].update(
                        {"final_value": winner, "status": "strong_majority_corrected"}
                    )
                record.issues = [
                    issue
                    for issue in record.issues
                    if not issue.startswith("院校代码网格单元格双路复识未达可信阈值")
                    and not issue.startswith("院校代码格式异常")
                    and issue != "缺少必填字段：院校代码"
                ]
                record.warnings.append(f"院校代码按同名院校强多数校正：{old}→{winner}")

    by_code: defaultdict[tuple[str, str, str], list[ParsedRecord]] = defaultdict(list)
    for record in records:
        key = (
            str(record.values.get("来源文件", "")).strip(),
            str(record.values.get("科类", "")).strip(),
            str(record.values.get("院校代码", "")).strip(),
        )
        by_code[key].append(record)
    for items in by_code.values():
        counts = Counter(
            str(item.values.get("院校名称", "")).strip()
            for item in items
            if str(item.values.get("院校名称", "")).strip()
        )
        if not counts:
            continue
        winner, count = counts.most_common(1)[0]
        if count < 2 or count / len(items) < 0.60:
            continue
        for record in items:
            old = str(record.values.get("院校名称", "")).strip()
            if old and old != winner and (
                SequenceMatcher(None, old, winner).ratio() >= 0.72
                or old.startswith(winner)
                or winner.startswith(old)
            ):
                record.values["院校名称"] = winner
                record.warnings.append(f"院校名称按同代码强多数校正：{old}→{winner}")

    verified_group_codes: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for record in records:
        verified_fields = set(record.values.get("_grid_verified_fields", []))
        if "专业组代码" not in verified_fields:
            continue
        key = (
            str(record.values.get("科类", "")).strip(),
            str(record.values.get("专业组名称", "")).strip(),
        )
        code = str(record.values.get("专业组代码", "")).strip()
        if all(key) and code:
            verified_group_codes[key][code] += 1
    strong_group_codes: dict[tuple[str, str], str] = {}
    for key, counts in verified_group_codes.items():
        winner, count = counts.most_common(1)[0]
        total = counts.total()
        if count >= 3 and count / total >= 0.98:
            strong_group_codes[key] = winner
    for record in records:
        key = (
            str(record.values.get("科类", "")).strip(),
            str(record.values.get("专业组名称", "")).strip(),
        )
        winner = strong_group_codes.get(key, "")
        if not winner:
            continue
        verified_fields = set(record.values.get("_grid_verified_fields", []))
        if "专业组代码" in verified_fields:
            continue
        old = str(record.values.get("专业组代码", "")).strip()
        record.values["专业组代码"] = winner
        evidence = record.values.get("_field_evidence", {})
        if isinstance(evidence, dict) and isinstance(evidence.get("专业组代码"), dict):
            evidence["专业组代码"].update(
                {"final_value": winner, "status": "strong_majority_corrected"}
            )
        record.issues = [
            issue
            for issue in record.issues
            if not issue.startswith("专业组代码网格单元格双路复识未达可信阈值")
        ]
        if old != winner:
            record.warnings.append(
                f"专业组代码按同科类同组名的已复识强多数校正：{old or '空'}→{winner}"
            )

    for record in records:
        institution = str(record.values.get("院校代码", ""))
        group = str(record.values.get("专业组代码", ""))
        record.values["院校专业组代码"] = institution + group if group else ""
        record.issues = _dedupe(record.issues)
        record.warnings = _dedupe(record.warnings)


def parse_ruled_grid_table(
    pages: list[ExtractedPage],
    profile: dict[str, Any],
    *,
    source_path: Path | None,
    context: dict[str, str],
    cell_ocr_cache_root: Path | None = None,
    diagnostics: ParseDiagnostics | None = None,
) -> list[ParsedRecord]:
    """Parse OCR pages whose visible ruled grid is more reliable than token rows."""
    if source_path is None or source_path.suffix.casefold() != ".pdf":
        raise RuntimeError("网格表解析器需要可读取的 PDF 源路径。")
    expected_lines = int(profile.get("expected_vertical_lines", 11))
    anchor_column = int(profile.get("anchor_column", 2))
    anchor_pattern = re.compile(str(profile.get("anchor_pattern", r"\d{4}")))
    field_columns = {
        str(field): tuple(int(value) for value in pair)
        for field, pair in (profile.get("grid_columns") or {}).items()
    }
    field_patterns = {
        str(field): re.compile(str(pattern))
        for field, pattern in (profile.get("critical_patterns") or {}).items()
    }
    required = [str(field) for field in profile.get("required", [])]
    critical_confidence = float(profile.get("critical_confidence", 0.88))
    cell_consensus_confidence = float(
        profile.get("cell_consensus_correction_confidence", critical_confidence)
    )
    always_cell_ocr_fields = {
        str(field) for field in profile.get("always_cell_ocr_fields", [])
    }
    conditional_cell_ocr_fields = {
        str(field) for field in profile.get("conditional_cell_ocr_fields", [])
    }
    reuse_cell_verified_fields = {
        str(field) for field in profile.get("reuse_cell_verified_fields", [])
    }
    recover_unanchored = bool(profile.get("recover_unanchored_grid_rows", False))
    row_presence_fields = [
        str(field)
        for field in profile.get(
            "row_presence_fields",
            ["专业代码", "专业名称及专业方向", "计划数"],
        )
    ]
    row_presence_min_fields = int(profile.get("row_presence_min_fields", 2))
    row_header_terms = [str(value) for value in profile.get("row_header_terms", [])]
    cache = _GridCellOcrCache(
        cell_ocr_cache_root,
        source_sha256=pages[0].source_sha256 if pages else "",
        profile_name=str(profile.get("name", "ruled-grid")),
    )
    document = pymupdf.open(source_path)
    ocr = RapidOCR(params={"Global.max_side_len": 4096})
    records: list[ParsedRecord] = []
    detected_units = 0
    recovered_units = 0
    semantically_verified: dict[tuple[str, str, str, str], str] = {}
    semantic_samples: defaultdict[
        tuple[str, str, str, str], Counter[str]
    ] = defaultdict(Counter)
    semantic_reuse_count = 0
    try:
        for page in pages:
            pdf_page = document[page.page_number - 1]
            width, height = int(page.width), int(page.height)
            horizontal = _detect_horizontal_lines(pdf_page, width, height)
            vertical = _detect_vertical_lines(pdf_page, width, height)
            if len(vertical) != expected_lines:
                raise RuntimeError(
                    f"{page.source_relative_path} 第{page.page_number}页表格边界不稳定："
                    f"预期{expected_lines}条竖线，实际{len(vertical)}条；已停止生成 Excel。"
                )
            grid_rows = _find_grid_rows(
                page,
                vertical=vertical,
                horizontal=horizontal,
                anchor_column=anchor_column,
                anchor_pattern=anchor_pattern,
                field_columns=field_columns,
                recover_unanchored=recover_unanchored,
                row_presence_fields=row_presence_fields,
                row_presence_min_fields=row_presence_min_fields,
                row_header_terms=row_header_terms,
            )
            detected_units += len(grid_rows)
            recovered_units += sum(row.recovered for row in grid_rows)
            page_image: Image.Image | None = None
            for row_number, row in enumerate(grid_rows, start=1):
                top, bottom, boundary_method = row.top, row.bottom, row.boundary_method
                cell_tokens = {
                    field: _tokens_in_cell(
                        page.tokens, vertical[left] + 1, vertical[right] - 1, top, bottom
                    )
                    for field, (left, right) in field_columns.items()
                }
                values = dict(context)
                values.update(
                    {
                        "院校代码": _normalize_code(row.anchor.text) if row.anchor else "",
                        **{
                            field: (
                                _normalize_code(_join_cell(tokens))
                                if field in field_patterns
                                else _join_cell(tokens)
                            )
                            for field, tokens in cell_tokens.items()
                        },
                        "来源文件": page.source_relative_path,
                        "来源页": page.page_number,
                        "来源行": row_number,
                        "提取方式": f"{page.method}+ruled_grid",
                        "_grid_boundary": boundary_method,
                        "_grid_row_recovered": row.recovered,
                    }
                )
                if "专业名称" in values and "专业名称及专业方向" not in values:
                    values["专业名称及专业方向"] = values.pop("专业名称")
                if "专业组名称" in values:
                    values.setdefault("院校专业组名称", values["专业组名称"])
                    values.setdefault("科目要求", values["专业组名称"])
                    values.setdefault("再选科目", _normalize_subject(str(values["专业组名称"])))

                issues: list[str] = []
                warnings: list[str] = []
                if row.recovered:
                    warnings.append("院校代码锚点异常，已按完整表格横线保留该专业行")
                    if row.presence_count < row_presence_min_fields:
                        issues.append("检测到表格数据行，但关键单元格OCR内容不足")
                if "专业代码" in field_patterns:
                    code, name, message = _separate_major_code_from_name(
                        str(values.get("专业代码", "")),
                        str(values.get("专业名称及专业方向", "")),
                        field_patterns["专业代码"],
                    )
                    values["专业代码"] = code
                    values["专业名称及专业方向"] = name
                    if message:
                        warnings.append(message)
                refined_scores: dict[str, float] = {}
                field_evidence: dict[str, dict[str, Any]] = {}
                for field, pattern in field_patterns.items():
                    current = str(values.get(field, "")).strip()
                    original_score = _score_floor(cell_tokens.get(field, []))
                    field_evidence[field] = {
                        "raw_value": current,
                        "raw_confidence": round(original_score, 5),
                        "cell_value": "",
                        "cell_confidence": 0.0,
                        "final_value": current,
                        "status": "unresolved",
                    }
                    reuse_key = _semantic_reuse_key(field, values)
                    reused = (
                        semantically_verified.get(reuse_key, "")
                        if field in reuse_cell_verified_fields and all(reuse_key)
                        else ""
                    )
                    if reused:
                        semantic_reuse_count += 1
                        values[field] = reused
                        field_evidence[field]["final_value"] = reused
                        field_evidence[field]["status"] = "verified_semantic_reuse"
                        verified_fields = set(values.get("_grid_verified_fields", []))
                        verified_fields.add(field)
                        values["_grid_verified_fields"] = sorted(verified_fields)
                        if current != reused:
                            warnings.append(
                                f"{field}按同文件同科类同标签的已复识值校正：{current or '空'}→{reused}"
                            )
                        continue
                    always_recheck = field in always_cell_ocr_fields
                    conditional_recheck = field in conditional_cell_ocr_fields
                    needs_refine = (
                        always_recheck
                        or not pattern.fullmatch(current)
                        or original_score < critical_confidence
                    )
                    if not needs_refine:
                        field_evidence[field]["status"] = "source_high_confidence"
                        continue
                    if field not in field_columns:
                        continue
                    page_image = page_image or _render_page(pdf_page, width, height)
                    left, right = field_columns[field]
                    second, score = cache.recognize(
                        page_number=page.page_number,
                        field=field,
                        image=page_image,
                        x1=vertical[left],
                        x2=vertical[right],
                        y1=top,
                        y2=bottom,
                        ocr=ocr,
                        pattern=pattern,
                    )
                    field_evidence[field]["cell_value"] = second
                    field_evidence[field]["cell_confidence"] = round(score, 5)
                    if always_recheck or conditional_recheck:
                        value, cell_issues, cell_warnings = _reconcile_cell_code(
                            field=field,
                            current=current,
                            current_score=original_score,
                            recognized=second,
                            score=score,
                            minimum_score=cell_consensus_confidence,
                            current_minimum_score=critical_confidence,
                            always_recheck=True,
                        )
                        values[field] = value
                        field_evidence[field]["final_value"] = value
                        issues.extend(cell_issues)
                        warnings.extend(cell_warnings)
                        if second and second == value and not cell_issues:
                            verified_fields = set(values.get("_grid_verified_fields", []))
                            verified_fields.add(field)
                            values["_grid_verified_fields"] = sorted(verified_fields)
                            field_evidence[field]["status"] = (
                                "cell_corrected" if value != current else "cell_verified"
                            )
                            if field in reuse_cell_verified_fields and all(reuse_key):
                                semantic_samples[reuse_key][value] += 1
                                winner, sample_count = semantic_samples[
                                    reuse_key
                                ].most_common(1)[0]
                                if sample_count >= 2:
                                    semantically_verified[reuse_key] = winner
                        elif not cell_issues and current and original_score >= critical_confidence:
                            field_evidence[field]["status"] = "source_high_confidence"
                        if value != current and not cell_issues:
                            refined_scores[field] = score
                    elif second and score >= critical_confidence and (
                        not pattern.fullmatch(current) or second == current
                    ):
                        values[field] = second
                        field_evidence[field]["final_value"] = second
                        field_evidence[field]["status"] = (
                            "cell_corrected" if second != current else "cell_verified"
                        )
                        refined_scores[field] = score
                        if current and second == current and original_score < critical_confidence:
                            warnings.append(f"{field}低置信度但单元格复识一致")
                    elif second and score >= critical_confidence and second != current:
                        issues.append(f"{field}整页OCR与单元格OCR不一致（{current}/{second}）")
                values["_field_evidence"] = field_evidence

                for field in required:
                    if not str(values.get(field, "")).strip():
                        issues.append(f"缺少必填字段：{field}")
                for field, pattern in field_patterns.items():
                    value = str(values.get(field, "")).strip()
                    if value and not pattern.fullmatch(value):
                        issues.append(f"{field}格式异常（{value}）")
                if str(values.get("专业组名称", "")).strip() and not str(
                    values.get("再选科目", "")
                ).strip():
                    issues.append("选科要求无法标准化")
                if boundary_method != "grid":
                    warnings.append("该行边界由相邻院校代码估算")

                critical_tokens = [row.anchor] if row.anchor is not None else []
                for field in field_columns:
                    critical_tokens.extend(cell_tokens[field])
                values["关键字段最低OCR置信度"] = round(
                    min(
                        [_score_floor(critical_tokens) or 1.0, *refined_scores.values()]
                    ),
                    5,
                )
                values["原始行文本"] = " | ".join(
                    str(values.get(field, ""))
                    for field in [
                        "院校代码", "院校名称", "专业组代码", "专业组名称",
                        "专业代码", "专业名称及专业方向", "计划数", "备注",
                    ]
                    if str(values.get(field, "")).strip()
                )
                records.append(
                    ParsedRecord(
                        values=values,
                        issues=_dedupe(issues),
                        warnings=_dedupe(warnings),
                    )
                )
    finally:
        document.close()
        cache.save()
    _reconcile_repeated_values(records)
    if diagnostics is not None:
        diagnostics.coverage_observable = True
        diagnostics.source_units_detected = detected_units
        diagnostics.records_emitted = len(records)
        diagnostics.recovered_units = recovered_units
        diagnostics.unparsed_units = detected_units - len(records)
        diagnostics.cell_ocr_cache_hits = cache.hits
        diagnostics.cell_ocr_cache_misses = cache.misses
        diagnostics.cell_ocr_semantic_reuse = semantic_reuse_count
    return records
