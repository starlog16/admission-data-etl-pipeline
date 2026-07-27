from pathlib import Path

from admission_pipeline.config import ProjectConfig
from admission_pipeline.cli import _compact_daily_result, build_parser
from admission_pipeline.exporting import _excel_scalar
from admission_pipeline.exporting import export_workbook
from admission_pipeline.domain import ParsedRecord
from openpyxl import load_workbook
from admission_pipeline.pipeline import (
    _aggregate_output_path,
    _archive_existing_output,
    _unverified_profile_names,
)


def test_directory_target_creates_one_mirrored_workbook(tmp_path: Path) -> None:
    raw_root = tmp_path / "input" / "raw"
    target = raw_root / "anhui" / "2026" / "19安徽"
    target.mkdir(parents=True)
    config = ProjectConfig(
        root=tmp_path,
        raw={
            "paths": {
                "input_root": "input/raw",
                "excel_root": "output/excel",
            }
        },
    )
    path = _aggregate_output_path(config, target)
    assert path == tmp_path / "output" / "excel" / "anhui" / "2026" / "19安徽" / "19安徽.xlsx"


def test_check_command_is_available_without_excel_output_flag() -> None:
    args = build_parser().parse_args(["check", "--input", "anhui/2026/demo"])
    assert args.command == "check"
    assert not hasattr(args, "overwrite")


def test_daily_command_has_no_overwrite_or_no_cache_escape_hatch() -> None:
    args = build_parser().parse_args(["daily", "--input", "anhui/2026/demo"])
    assert args.command == "daily"
    assert not hasattr(args, "overwrite")
    assert not hasattr(args, "no_cache")


def test_metadata_mapping_is_serialized_for_excel() -> None:
    assert _excel_scalar({"b": 2, "a": 1}) == '{"a": 1, "b": 2}'


def test_daily_output_is_compact() -> None:
    compact = _compact_daily_result(
        {
            "status": "completed",
            "target": "demo",
            "file_count": 2,
            "files": [{"cache_hit": True}, {"cache_hit": False}],
            "summary": {
                "record_count": 10,
                "review_count": 1,
                "duplicate_count": 0,
                "profiles": {"demo": 10},
            },
            "outputs": [{"path": "result.xlsx"}],
            "report_path": "report.json",
        }
    )
    assert compact["cache_hit_count"] == 1
    assert compact["new_extraction_count"] == 1
    assert "files" not in compact


def test_existing_output_is_archived_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "output" / "excel" / "anhui" / "2026" / "2026.xlsx"
    output.parent.mkdir(parents=True)
    output.write_text("old", encoding="utf-8")
    config = ProjectConfig(
        root=tmp_path,
        raw={
            "paths": {
                "excel_root": "output/excel",
                "archive_root": "output/archive",
            }
        },
    )
    archived = _archive_existing_output(config, output)
    assert archived.read_text(encoding="utf-8") == "old"
    assert not output.exists()


def test_unknown_profile_blocks_final_export() -> None:
    assert _unverified_profile_names(
        {
            "known": {"name": "known", "review_all": False},
            "generic": {"name": "generic-review", "review_all": True},
        }
    ) == ["generic-review"]


def test_hierarchical_output_adds_group_and_institution_summaries(tmp_path: Path) -> None:
    output = tmp_path / "result.xlsx"
    records = [
        ParsedRecord(values={
            "院校全称": "测试大学（河北）", "院校计划总数": "2",
            "院校专业组代码": "000101", "科目要求": "不限",
            "院校专业组名称": "测试大学01组", "专业组计划数": "2",
            "专业代码": "01", "计划数": "2", "来源页": "1", "来源文件": "demo.pdf",
        })
    ]
    export_workbook(output, records, {"record_count": 1}, ["院校专业组代码", "专业代码"])
    workbook = load_workbook(output, read_only=True)
    try:
        assert "专业组汇总" in workbook.sheetnames
        assert "院校汇总" in workbook.sheetnames
        group_headers = {
            cell.value: cell.column for cell in workbook["专业组汇总"][1]
        }
        institution_headers = {
            cell.value: cell.column for cell in workbook["院校汇总"][1]
        }
        assert workbook["专业组汇总"].cell(
            row=2, column=group_headers["校验结果"]
        ).value == "一致"
        assert workbook["院校汇总"].cell(
            row=2, column=institution_headers["校验结果"]
        ).value == "一致"
    finally:
        workbook.close()


def test_export_serializes_visible_container_values(tmp_path: Path) -> None:
    output = tmp_path / "container.xlsx"
    record = ParsedRecord(
        values={"院校代码": "0001", "辅助信息": []},
        issues=[],
    )
    export_workbook(output, [record], {"record_count": 1}, ["院校代码"])
    workbook = load_workbook(output, read_only=True)
    try:
        assert workbook["data"].cell(row=2, column=2).value == "[]"
    finally:
        workbook.close()
