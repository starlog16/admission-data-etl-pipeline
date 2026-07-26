from __future__ import annotations

import importlib
import importlib.metadata
import platform
import sys
from pathlib import Path
from typing import Any

from .config import ProjectConfig


REQUIRED_MODULES = {
    "rapidocr": "rapidocr",
    "onnxruntime": "onnxruntime",
    "PyMuPDF": "pymupdf",
    "Pillow": "PIL",
    "openpyxl": "openpyxl",
    "pandas": "pandas",
    "PyYAML": "yaml",
}


def diagnose(config: ProjectConfig) -> dict[str, Any]:
    modules: dict[str, Any] = {}
    ok = sys.version_info[:2] == (3, 12)
    for display_name, import_name in REQUIRED_MODULES.items():
        try:
            module = importlib.import_module(import_name)
            modules[display_name] = {
                "ok": True,
                "version": getattr(module, "__version__", None)
                or importlib.metadata.version(import_name),
            }
        except Exception as exc:  # pragma: no cover - diagnostic surface
            modules[display_name] = {"ok": False, "error": str(exc)}
            ok = False

    paths = {}
    for key in (
        "input_root",
        "excel_root",
        "archive_root",
        "review_root",
        "temp_root",
        "logs_root",
        "profiles_root",
    ):
        path = config.path(key)
        paths[key] = {"path": str(path), "exists": path.exists()}
        if key in {"input_root", "profiles_root"} and not path.exists():
            ok = False

    return {
        "ok": ok,
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "project_root": str(config.root),
        "is_project_venv": Path(sys.executable).resolve().is_relative_to(config.root / ".venv"),
        "modules": modules,
        "paths": paths,
    }
