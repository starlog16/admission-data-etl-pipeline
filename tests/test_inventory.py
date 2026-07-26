from pathlib import Path

from PIL import Image
from openpyxl import Workbook

from admission_pipeline.config import load_project_config
from admission_pipeline.inventory import inventory, resolve_input_target


def test_inventory_recurses_and_reads_image_metadata(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "input" / "raw" / "demo").mkdir(parents=True)
    (tmp_path / "output" / "excel").mkdir(parents=True)
    (tmp_path / "output" / "review").mkdir(parents=True)
    (tmp_path / "temp").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "config" / "profiles").mkdir()
    (tmp_path / "config" / "default.yaml").write_text(
        """
paths:
  input_root: input/raw
  excel_root: output/excel
  review_root: output/review
  temp_root: temp
  logs_root: logs
  profiles_root: config/profiles
extraction:
  image_extensions: [.png]
  pdf_extensions: [.pdf]
  ocr_global_max_side_len: 10000
parsing: {}
export: {}
""",
        encoding="utf-8",
    )
    image_path = tmp_path / "input" / "raw" / "demo" / "sample.png"
    Image.new("RGB", (12, 34), "white").save(image_path)
    config = load_project_config(tmp_path)
    target = resolve_input_target(config, "demo")
    result = inventory(config, target)
    assert len(result) == 1
    assert result[0].kind == "image"
    assert (result[0].width, result[0].height) == (12, 34)


def test_inventory_accepts_original_xlsx(tmp_path: Path) -> None:
    (tmp_path / "config" / "profiles").mkdir(parents=True)
    (tmp_path / "input" / "raw" / "demo").mkdir(parents=True)
    for name in ("output/excel", "output/review", "temp", "logs"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "config" / "default.yaml").write_text(
        """
paths:
  input_root: input/raw
  excel_root: output/excel
  review_root: output/review
  temp_root: temp
  logs_root: logs
  profiles_root: config/profiles
extraction:
  image_extensions: [.png]
  pdf_extensions: [.pdf]
  spreadsheet_extensions: [.xlsx]
  ocr_global_max_side_len: 10000
parsing: {}
export: {}
""",
        encoding="utf-8",
    )
    workbook = Workbook()
    workbook.active.append(["院校代码", "院校名称"])
    workbook.save(tmp_path / "input" / "raw" / "demo" / "sample.xlsx")
    config = load_project_config(tmp_path)
    result = inventory(config, resolve_input_target(config, "demo"))
    assert [(item.kind, item.page_count) for item in result] == [("spreadsheet", 1)]
