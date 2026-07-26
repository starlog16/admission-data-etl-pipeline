from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    root: Path
    raw: dict[str, Any]

    def path(self, key: str) -> Path:
        return (self.root / self.raw["paths"][key]).resolve()

    @property
    def extraction(self) -> dict[str, Any]:
        return self.raw["extraction"]

    @property
    def parsing(self) -> dict[str, Any]:
        return self.raw["parsing"]

    @property
    def export(self) -> dict[str, Any]:
        return self.raw["export"]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须是 YAML 对象：{path}")
    return value


def load_project_config(root: Path) -> ProjectConfig:
    root = root.resolve()
    return ProjectConfig(root=root, raw=load_yaml(root / "config" / "default.yaml"))


def load_profiles(config: ProjectConfig) -> list[dict[str, Any]]:
    profiles_dir = config.path("profiles_root")
    profiles = []
    for path in sorted(profiles_dir.glob("*.yaml")):
        profile = load_yaml(path)
        profile["_path"] = str(path)
        profiles.append(profile)
    return sorted(profiles, key=lambda item: int(item.get("priority", 0)), reverse=True)
