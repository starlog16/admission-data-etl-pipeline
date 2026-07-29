from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class InputFile:
    path: Path
    relative_path: Path
    kind: str
    size_bytes: int
    sha256: str
    page_count: int | None = None
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        data["relative_path"] = self.relative_path.as_posix()
        return data


@dataclass(slots=True)
class Token:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y2 - self.y1)


@dataclass(slots=True)
class ExtractedPage:
    source_relative_path: str
    source_sha256: str
    page_number: int
    method: str
    width: float
    height: float
    tokens: list[Token] = field(default_factory=list)
    plain_text: str = ""
    sheet_name: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtractedPage":
        copied = dict(data)
        copied["tokens"] = [Token(**item) for item in copied.get("tokens", [])]
        return cls(**copied)


@dataclass(slots=True)
class ParsedRecord:
    values: dict[str, Any]
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)

    @property
    def needs_audit(self) -> bool:
        return bool(self.warnings) and not self.needs_review

    @property
    def quality_bucket(self) -> str:
        if self.needs_review:
            return "review"
        if self.needs_audit:
            return "audit"
        return "data"


@dataclass(slots=True)
class ParseDiagnostics:
    parser: str
    coverage_observable: bool = False
    source_units_detected: int = 0
    records_emitted: int = 0
    recovered_units: int = 0
    unparsed_units: int = 0
    cell_ocr_cache_hits: int = 0
    cell_ocr_cache_misses: int = 0
    cell_ocr_semantic_reuse: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def coverage_ratio(self) -> float | None:
        if not self.coverage_observable or self.source_units_detected == 0:
            return None
        return self.records_emitted / self.source_units_detected

    def assert_complete(self) -> None:
        if not self.coverage_observable:
            return
        if self.unparsed_units or self.records_emitted != self.source_units_detected:
            raise RuntimeError(
                f"解析完整性保护触发：检测到 {self.source_units_detected} 个源数据单元，"
                f"输出 {self.records_emitted} 条，未保留 {self.unparsed_units} 条。"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser": self.parser,
            "coverage_observable": self.coverage_observable,
            "source_units_detected": self.source_units_detected,
            "records_emitted": self.records_emitted,
            "recovered_units": self.recovered_units,
            "unparsed_units": self.unparsed_units,
            "coverage_ratio": self.coverage_ratio,
            "cell_ocr_cache_hits": self.cell_ocr_cache_hits,
            "cell_ocr_cache_misses": self.cell_ocr_cache_misses,
            "cell_ocr_semantic_reuse": self.cell_ocr_semantic_reuse,
            "notes": list(self.notes),
        }
