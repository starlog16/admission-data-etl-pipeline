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

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)
