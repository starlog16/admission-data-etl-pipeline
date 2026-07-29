from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from .config import ProjectConfig, load_profiles
from .domain import ParseDiagnostics, ParsedRecord
from .exporting import export_workbook
from .extraction import ExtractionEngine
from .inventory import inventory, resolve_input_target
from .parsing import parse_pages, recheck_plan_totals
from .profiles import select_profile
from .refinement import apply_verified_record_corrections, refine_missing_numeric_cells
from .validation import validate_records


def inventory_report(config: ProjectConfig, user_input: str) -> dict[str, Any]:
    target = resolve_input_target(config, user_input)
    files = inventory(config, target)
    return {
        "target": str(target),
        "file_count": len(files),
        "image_count": sum(item.kind == "image" for item in files),
        "pdf_count": sum(item.kind == "pdf" for item in files),
        "spreadsheet_count": sum(item.kind == "spreadsheet" for item in files),
        "total_bytes": sum(item.size_bytes for item in files),
        "files": [item.to_dict() for item in files],
    }


def process(
    config: ProjectConfig,
    user_input: str,
    *,
    mode: str,
    requested_profile: str = "auto",
    use_cache: bool = True,
    force_ocr: bool = False,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    if mode not in {"extract", "check", "run"}:
        raise ValueError(f"未知运行模式：{mode}")
    started_at = datetime.now().isoformat(timespec="seconds")
    process_started = perf_counter()
    target = resolve_input_target(config, user_input)
    files = inventory(config, target)
    profiles = load_profiles(config)
    engine = ExtractionEngine(config)
    all_records: list[ParsedRecord] = []
    records_by_profile: dict[str, int] = {}
    used_profiles: dict[str, dict[str, Any]] = {}
    file_reports: list[dict[str, Any]] = []
    stage_totals = {
        "extract": 0.0,
        "profile": 0.0,
        "parse": 0.0,
        "refine_and_validate": 0.0,
    }

    for item in files:
        file_started = perf_counter()
        stage_started = perf_counter()
        pages = engine.extract(item, use_cache=use_cache, force_ocr=force_ocr)
        extract_seconds = perf_counter() - stage_started
        stage_totals["extract"] += extract_seconds
        document_text = "\n".join(
            page.plain_text
            or " ".join(token.text for token in page.tokens if token.text.strip())
            for page in pages
        )
        stage_started = perf_counter()
        profile = select_profile(
            profiles,
            item.relative_path,
            requested_profile,
            document_text,
        )
        profile_seconds = perf_counter() - stage_started
        stage_totals["profile"] += profile_seconds
        file_report: dict[str, Any] = {
            "source": item.relative_path.as_posix(),
            "kind": item.kind,
            "profile": profile["name"],
            "pages": len(pages),
            "methods": sorted({page.method for page in pages}),
            "tokens": sum(len(page.tokens) for page in pages),
            "cache_hit": engine.last_cache_hit,
        }
        if mode in {"check", "run"}:
            diagnostics = ParseDiagnostics(parser=str(profile.get("parser", "")))
            stage_started = perf_counter()
            records = parse_pages(
                pages,
                profile,
                y_tolerance_ratio=float(config.parsing["row_y_tolerance_ratio"]),
                source_path=item.path,
                cell_ocr_cache_root=(
                    config.path("temp_root") / "cache" / "grid_cell_ocr"
                ),
                diagnostics=diagnostics,
            )
            parse_seconds = perf_counter() - stage_started
            stage_totals["parse"] += parse_seconds
            stage_started = perf_counter()
            if (profile.get("cell_ocr_refinement") or {}).get("enabled", False):
                records = refine_missing_numeric_cells(
                    config, item, pages, records, profile, engine.ocr
                )
            records = apply_verified_record_corrections(records, profile)
            recheck_plan_totals(records)
            for record in records:
                record.values["版式"] = profile["name"]
            if not diagnostics.coverage_observable:
                diagnostics.records_emitted = len(records)
                diagnostics.notes.append(
                    "该解析器尚不能从版面独立计数源记录；不得把输出行数当作完整率。"
                )
            diagnostics.assert_complete()
            refine_seconds = perf_counter() - stage_started
            stage_totals["refine_and_validate"] += refine_seconds
            all_records.extend(records)
            records_by_profile[profile["name"]] = (
                records_by_profile.get(profile["name"], 0) + len(records)
            )
            used_profiles[profile["name"]] = profile
            file_report["records"] = len(records)
            file_report["review_records"] = sum(record.needs_review for record in records)
            file_report["warning_records"] = sum(bool(record.warnings) for record in records)
            file_report["parse_diagnostics"] = diagnostics.to_dict()
            file_report["timings_seconds"] = {
                "extract": round(extract_seconds, 4),
                "profile": round(profile_seconds, 4),
                "parse": round(parse_seconds, 4),
                "refine_and_validate": round(refine_seconds, 4),
                "total": round(perf_counter() - file_started, 4),
            }
            file_report["review_examples"] = [
                {"issues": record.issues, "values": record.values}
                for record in records
                if record.needs_review
            ][:3]
        else:
            file_report["timings_seconds"] = {
                "extract": round(extract_seconds, 4),
                "profile": round(profile_seconds, 4),
                "total": round(perf_counter() - file_started, 4),
            }
        file_reports.append(file_report)

    report: dict[str, Any] = {
        "mode": mode,
        "target": str(target),
        "started_at": started_at,
        "file_count": len(files),
        "files": file_reports,
        "outputs": [],
    }
    if mode in {"check", "run"}:
        summary = validate_records(all_records, "aggregate")
        summary["profiles"] = records_by_profile
        summary["source_file_count"] = len(files)
        _add_coverage_release_gate(summary, file_reports, len(files))
        diagnostics_items = [
            item["parse_diagnostics"]
            for item in file_reports
            if item.get("parse_diagnostics")
        ]
        summary["cell_ocr"] = {
            "cache_hits": sum(
                int(item.get("cell_ocr_cache_hits", 0)) for item in diagnostics_items
            ),
            "cache_misses": sum(
                int(item.get("cell_ocr_cache_misses", 0)) for item in diagnostics_items
            ),
            "semantic_reuse": sum(
                int(item.get("cell_ocr_semantic_reuse", 0))
                for item in diagnostics_items
            ),
        }
        report["summary"] = summary

    if mode == "run":
        if not all_records:
            raise RuntimeError("未解析出任何数据记录，已停止生成 Excel。请检查版式匹配和源文件内容。")
        unverified_profiles = _unverified_profile_names(used_profiles)
        if unverified_profiles:
            raise RuntimeError(
                "发现未验证版式，已停止生成 Excel："
                + "、".join(unverified_profiles)
                + "。请先新增或修正版式 YAML，再重新运行。"
            )
        if not bool(summary.get("release_safe", False)):
            raise RuntimeError(
                "质量保护触发："
                + "；".join(str(value) for value in summary.get("release_blockers", []))
                + "。已停止生成 Excel。"
            )
        output_path = _aggregate_output_path(config, target)
        if output_path.exists() and not overwrite and not archive_existing:
            raise FileExistsError(
                f"汇总文件已存在：{output_path}。确认覆盖后使用 --overwrite。"
            )
        code_fields = sorted(
            {
                field
                for profile in used_profiles.values()
                for field in profile.get("code_fields", [])
            }
        )
        export_target = output_path
        if output_path.exists() and archive_existing:
            export_target = (
                config.path("temp_root")
                / "output_staging"
                / f"{uuid4().hex}.xlsx"
            )
        export_workbook(
            export_target,
            all_records,
            summary,
            code_fields=code_fields,
        )
        if export_target != output_path:
            try:
                archived_output = _archive_existing_output(config, output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                export_target.replace(output_path)
            except Exception:
                export_target.unlink(missing_ok=True)
                raise
            report["archived_output"] = str(archived_output)
        report["outputs"].append({"path": str(output_path), "summary": summary})

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["timings_seconds"] = {
        **{key: round(value, 4) for key, value in stage_totals.items()},
        "total": round(perf_counter() - process_started, 4),
    }
    report_path = _write_report(config, report, target.name)
    report["report_path"] = str(report_path)
    return report


def _add_coverage_release_gate(
    summary: dict[str, Any],
    file_reports: list[dict[str, Any]],
    source_file_count: int,
) -> None:
    observable = [
        item["parse_diagnostics"]
        for item in file_reports
        if item.get("parse_diagnostics", {}).get("coverage_observable")
    ]
    unobservable_file_count = source_file_count - len(observable)
    summary["coverage"] = {
        "observable_file_count": len(observable),
        "unobservable_file_count": unobservable_file_count,
        "source_units_detected": sum(
            int(item["source_units_detected"]) for item in observable
        ),
        "records_emitted": sum(int(item["records_emitted"]) for item in observable),
        "recovered_units": sum(int(item["recovered_units"]) for item in observable),
        "unparsed_units": sum(int(item["unparsed_units"]) for item in observable),
    }
    if unobservable_file_count:
        summary["release_safe"] = False
        blockers = summary.setdefault("release_blockers", [])
        blockers.append(
            f"{unobservable_file_count}个源文件的解析器尚不能独立证明完整率"
        )


def _aggregate_output_path(config: ProjectConfig, target: Path) -> Path:
    raw_root = config.path("input_root")
    relative = target.relative_to(raw_root)
    if target.is_file():
        output_dir = config.path("excel_root") / relative.parent
        name = target.stem
    else:
        output_dir = config.path("excel_root") / relative
        name = target.name
    safe_name = "".join(
        character if character not in '<>:"/\\|?*' else "_" for character in name
    )
    return output_dir / f"{safe_name}.xlsx"


def _unverified_profile_names(
    used_profiles: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted(
        profile["name"]
        for profile in used_profiles.values()
        if profile.get("review_all", False)
    )


def _archive_existing_output(config: ProjectConfig, output_path: Path) -> Path:
    relative = output_path.relative_to(config.path("excel_root"))
    archive_dir = config.path("archive_root") / relative.parent
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = archive_dir / f"{timestamp}_{output_path.name}"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"{timestamp}_{counter}_{output_path.name}"
        counter += 1
    output_path.replace(candidate)
    return candidate


def _write_report(config: ProjectConfig, report: dict[str, Any], target_name: str) -> Path:
    logs_root = config.path("logs_root")
    logs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in target_name)
    path = logs_root / f"{timestamp}_{safe_name}_{report['mode']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
