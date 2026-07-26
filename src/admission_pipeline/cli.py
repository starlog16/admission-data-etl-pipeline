from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_profiles, load_project_config
from .doctor import diagnose
from .pipeline import inventory_report, process


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _compact_daily_result(result: dict[str, object]) -> dict[str, object]:
    files = list(result.get("files", []))
    summary = dict(result.get("summary", {}))
    outputs = list(result.get("outputs", []))
    output_path = outputs[0]["path"] if outputs else None
    return {
        "status": result.get("status", "completed"),
        "target": result.get("target"),
        "file_count": result.get("file_count", 0),
        "new_extraction_count": sum(
            not bool(item.get("cache_hit")) for item in files
        ),
        "cache_hit_count": sum(bool(item.get("cache_hit")) for item in files),
        "record_count": summary.get("record_count", 0),
        "review_count": summary.get("review_count", 0),
        "duplicate_count": summary.get("duplicate_count", 0),
        "profiles": summary.get("profiles", {}),
        "output_path": output_path,
        "archived_output": result.get("archived_output"),
        "report_path": result.get("report_path"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="招生数据 PDF/图片转 Excel 批处理流水线"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="检查 Python、依赖和项目目录")
    subparsers.add_parser("profiles", help="列出可用的版式配置")

    inventory_parser = subparsers.add_parser("inventory", help="只统计文件，不做 OCR")
    inventory_parser.add_argument("--input", required=True, help="input/raw 下的相对路径或绝对路径")

    daily_parser = subparsers.add_parser(
        "daily", help="日常一键处理：复用缓存、校验并生成唯一汇总 Excel"
    )
    daily_parser.add_argument("--input", required=True)
    daily_parser.add_argument("--profile", default="auto")

    for name, help_text in (
        ("extract", "提取并缓存文字，不解析、不生成 Excel"),
        ("check", "提取、解析和校验，但不生成 Excel"),
        ("run", "提取、解析、校验并生成 Excel"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("--input", required=True)
        command_parser.add_argument("--profile", default="auto")
        command_parser.add_argument("--force-ocr", action="store_true")
        command_parser.add_argument("--no-cache", action="store_true")
        if name == "run":
            command_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(project_root: Path | None = None, argv: list[str] | None = None) -> int:
    root = (project_root or Path.cwd()).resolve()
    config = load_project_config(root)
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        result = diagnose(config)
        _print_json(result)
        return 0 if result["ok"] else 1
    if args.command == "profiles":
        _print_json(
            [
                {"name": profile["name"], "description": profile.get("description", "")}
                for profile in load_profiles(config)
            ]
        )
        return 0
    if args.command == "inventory":
        _print_json(inventory_report(config, args.input))
        return 0
    if args.command == "daily":
        health = diagnose(config)
        if not health["ok"]:
            _print_json({"status": "environment_error", "doctor": health})
            return 1
        try:
            result = process(
                config,
                args.input,
                mode="run",
                requested_profile=args.profile,
                use_cache=True,
                force_ocr=False,
                overwrite=False,
                archive_existing=True,
            )
        except PermissionError:
            _print_json(
                {
                    "status": "stopped",
                    "error": "当前汇总 Excel 正被其他程序占用。请关闭 Excel 或预览窗口后，重新执行同一条 /pdf-to-excel 命令。",
                }
            )
            return 2
        except (FileExistsError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            _print_json({"status": "stopped", "error": str(exc)})
            return 2
        result["status"] = "completed"
        _print_json(_compact_daily_result(result))
        return 0
    result = process(
        config,
        args.input,
        mode=args.command,
        requested_profile=args.profile,
        use_cache=not args.no_cache,
        force_ocr=args.force_ocr,
        overwrite=getattr(args, "overwrite", False),
    )
    _print_json(result)
    return 0
