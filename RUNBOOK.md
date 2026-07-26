# 招生数据流水线使用手册

## 这套流水线解决什么

统一处理以下输入：

- JPG、PNG、WEBP、TIFF 等图片；
- 可以复制文字的文本 PDF；
- 每页都是图片的扫描 PDF；
- 原始 XLSX、XLSM 表格；
- 同一任务目录中混合存在的图片和 PDF。

程序会自动选择：

```text
图片 → RapidOCR
文本 PDF → PyMuPDF 直接提取
扫描 PDF → 自动渲染页面 → RapidOCR
原始 Excel → 直接读取单元格
```

OCR 结果按文件内容缓存。调整版式配置或字段映射时不会重新 OCR。

密集双栏计划表会对首次 OCR 漏掉的小号计划数执行局部单元格补识别。补识别结果只有在“专业计划合计 = 原始院校计划总数”或反向校验成立时才进入正式字段；否则仅作为候选值进入 `review`。

## 每次开始前

在项目根目录打开终端：

```powershell
cd "C:\Users\Saturn\Desktop\work\pdf_to_excel_pipeline"
```

检查环境：

```powershell
scripts\run.cmd doctor
```

列出已有版式：

```powershell
scripts\run.cmd profiles
```

## 第一次接收一批文件

把完整原始任务目录放入：

```text
input\raw\省份英文名\年份\任务名\
```

不要拆散原有子目录，也不要按 PDF/图片重新分类。

只统计文件，不做 OCR：

```powershell
scripts\run.cmd inventory --input "anhui\2026\19安徽"
```

首次提取并建立缓存，但不生成 Excel：

```powershell
scripts\run.cmd extract --input "anhui\2026\19安徽"
```

解析和校验缓存，但不生成 Excel：

```powershell
scripts\run.cmd check --input "anhui\2026\19安徽"
```

确认版式配置以后，复用缓存生成结果：

```powershell
scripts\run.cmd run --input "anhui\2026\19安徽"
```

日常已验证版式推荐直接使用安全的一键命令：

```powershell
scripts\run.cmd daily --input "anhui\2026\19安徽"
```

`daily` 不支持强制重做 OCR 或覆盖已有结果；发现未知版式时会停止生成。

如果同名工作簿已存在，`daily` 会先把旧文件归档到 `output/archive/`，再生成新的唯一当前结果。

无论目标目录中有多少图片、PDF和子文件夹，每次 `run` 只生成一个汇总工作簿：

```text
output\excel\anhui\2026\19安徽\19安徽.xlsx
```

工作簿中：

- `data`：所有通过校验的记录；
- `review`：OCR异常、缺字段或未知版式记录；
- `metadata`：文件数、记录数、版式和校验摘要。

如果要把某省某一年的所有任务合成一份文件，目标直接指定年份目录：

```powershell
scripts\run.cmd run --input "anhui\2026"
```

输出将是 `output\excel\anhui\2026\2026.xlsx`。

已有同名汇总文件时程序默认停止，确认覆盖后才使用 `--overwrite`。报告写入 `logs\`。

## 换省份时怎样复用

程序优先根据文档标题、表头和数据层级匹配 `config\profiles\*.yaml`，省份和文件路径只作为必要时的辅助条件。配置代表“结构家族”，不代表某个省份。

- 双栏“院校行 + 专业行”征集计划：自动使用 `two-column-recruitment-plan`。
- 单栏“院校→专业组→专业”余缺计划：自动使用 `hierarchical-group-major-plan`。
- 原始 Excel 专业组计划：自动使用 `spreadsheet-group-plan`，不做 OCR。
- 专业明细计划、专业组投档线和院校投档线：按表头自动匹配对应结构家族。
- 同一结构家族只是标题、年份或省份变化：直接运行，不改代码。
- 字段相同但列位置明显变化：优先扩展现有结构家族的坐标识别，不为每个文件新建配置。
- 数据层级变化，例如新增“专业组”层：增加一个新的结构家族。
- 完全不同的复杂结构：才需要新增解析器代码。

真正的新结构第一次建议先运行 `extract` 和 `check`。确认 OCR、字段映射和校验正常后，再处理整个任务目录；后续同结构文件直接使用日常命令。

## 三个运行级别

```text
inventory  只列清单，几乎瞬间完成
extract    提取文字并缓存，不生成 Excel
run        复用缓存，解析、校验并生成 Excel
```

需要忽略缓存重新 OCR 时才使用：

```powershell
scripts\run.cmd extract --input "省份\年份\任务" --no-cache
```

文本 PDF 被错误判断时，可强制按扫描件 OCR：

```powershell
scripts\run.cmd extract --input "省份\年份\任务\文件.pdf" --force-ocr
```

这两个参数会显著增加时间，日常不要使用。

## 模型与 CC Switch 的边界

本流水线运行时不调用 Claude、OpenAI 或第三方模型 API，也不依赖 CC Switch 路由。

模型只用于：

- 判断一种全新版式如何配置；
- 修改少量 YAML 或解析器代码；
- 查看待复核的少量异常样本。

因此第三方路由的 403/422、视觉协议兼容和 Token 费用不会影响正常批处理。

## Claude Code 中的命令

日常使用时直接输入：

```text
/pdf-to-excel anhui/2026
```

Skill 内部只会执行：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_pipeline.py daily --input "anhui/2026/19安徽"
```

不要在 Claude Code 中使用临时 `python -c` 或直接调用包内部函数。

## 当前限制

- “通用”指统一处理入口和大部分表格版式，不代表任何未知排版都能零配置准确入库。
- 新省份若字段含义不同，必须先补充 `reference\field_spec.md`。
- 低置信度和格式异常必须进入复核，不能为了自动化而猜值。
