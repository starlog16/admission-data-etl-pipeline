from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _all_present(haystack: str, needles: list[str]) -> bool:
    normalized = haystack.casefold()
    return all(needle.casefold() in normalized for needle in needles)


def _all_text_present(haystack: str, needles: list[str]) -> bool:
    normalized = re.sub(r"\s+", "", haystack).casefold()
    return all(re.sub(r"\s+", "", needle).casefold() in normalized for needle in needles)


def _all_text_patterns(haystack: str, patterns: list[str]) -> bool:
    normalized = re.sub(r"\s+", "", haystack)
    return all(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def profile_matches(
    profile: dict[str, Any], relative_path: Path, document_text: str = ""
) -> bool:
    match = profile.get("match") or {}
    if not match:
        return True
    full = relative_path.as_posix()
    filename = relative_path.name
    return (
        _all_present(full, match.get("path_contains", []))
        and _all_present(filename, match.get("filename_contains", []))
        and _all_text_present(document_text, match.get("text_contains", []))
        and _all_text_patterns(document_text, match.get("text_patterns", []))
    )


def select_profile(
    profiles: list[dict[str, Any]],
    relative_path: Path,
    requested: str = "auto",
    document_text: str = "",
) -> dict[str, Any]:
    if requested != "auto":
        for profile in profiles:
            if profile.get("name") == requested:
                return profile
        raise ValueError(f"找不到版式配置：{requested}")
    for profile in profiles:
        if profile_matches(profile, relative_path, document_text):
            return profile
    raise RuntimeError("没有可用的默认版式配置")
