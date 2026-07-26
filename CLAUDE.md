# 招生 PDF、图片、Excel 统一整理项目规则

## 项目目标

把各省高考招生 PDF、扫描 PDF、图片和原始 Excel 批量转换为可校验、可追溯的结构化 Excel。

## 目录与安全

- `input/raw/` 是只读原始数据，禁止修改、移动、重命名或删除。
- 最终 Excel 只写入 `output/excel/`，待复核数据写入 `output/review/`。
- 每次指定一个任务、年份或省份目录时，只生成一个汇总工作簿；禁止按图片或 PDF 分别生成多个工作簿。
- 汇总工作簿固定使用 `data`、`review`、`metadata`；存在层级计划时可增加 `专业组汇总`、`院校汇总` 校验表。
- OCR 缓存只写入 `temp/cache/`，运行报告只写入 `logs/`。
- 禁止在代码、配置和日志中保存密码、Token、Cookie、私钥或数据库连接串。
- 未经用户明确授权，不连接数据库、不执行 Git 推送、不覆盖已有正式结果。

## 固定运行环境

- 只能使用项目根目录的 `.venv\Scripts\python.exe`。
- Python 固定为 3.12；禁止向全局 Python 3.14 安装依赖。
- OCR 使用 `rapidocr` 与 `onnxruntime`，禁止使用旧包 `rapidocr_onnxruntime`。
- PDF 使用 `pymupdf`；代码中使用 `import pymupdf`，禁止安装无关的 `fitz` 包。
- 用户在 PowerShell 中手动运行时，通过 `scripts\run.cmd` 执行。
- Claude Code 的 Bash 工具必须直接执行 `.venv/Scripts/python.exe scripts/run_pipeline.py`，不要用 `cmd /c` 包装。

## Claude Code 强制执行边界

- 日常处理只能由用户主动输入 `/pdf-to-excel 省份/年份[/任务]` 触发；不得自动调用该 Skill。
- 用户要求日常批量生成结果时，只允许执行：
  `PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_pipeline.py daily --input "目标相对路径"`。
- 用户要求测试但不生成 Excel 时，只允许依次使用 `doctor`、`inventory`、`extract`、`check` 四个正式子命令。
- 禁止使用 `python -c`、临时脚本或直接导入 `admission_pipeline` 内部模块完成日常任务。
- 禁止在日常任务中读取、重写或调试公共源码；禁止启动 Plan、Explore、子代理或 worktree。
- 正式命令失败时，立即停止并原样报告命令、退出码和错误；除非用户明确说“修复项目”，否则不得自行排查内部接口或修改代码。
- 除非用户明确要求“适配新版式”，否则不得修改 `config/profiles/`。
- 禁止自动使用 `--no-cache`、`--force-ocr` 或 `--overwrite`。
- `daily` 重复运行时只允许把旧工作簿移动到 `output/archive/`，再生成唯一最新结果；不得删除旧结果。
- 不得把完整 OCR 文本打印到对话中，只汇报文件数、记录数、复核数、使用版式、输出路径和日志路径。

## 低耗时、低 Token 原则

- 常规处理禁止进入 Plan 模式，禁止启动 Explore/Plan 子代理或 worktree。
- 不要把整份 PDF、全部图片或完整 OCR 文本读入模型上下文。
- 日常任务直接执行固定 `daily` 命令；批量提取交给本地脚本。
- OCR 结果必须复用 `temp/cache/`；只有源文件或提取配置变化时才重新 OCR。
- 调整列名、列边界、必填项时优先修改 `config/profiles/*.yaml`，不要重写公共代码。
- 只有出现全新版式、异常行或校验失败时才让模型查看少量代表样本。
- 环境检查和代码测试不得生成正式 Excel。

## 数据规则

- 代码字段按文本保存并保留前导零。
- 不得猜测无法确认的数据；异常值进入待复核结果并说明原因。
- 输出必须保留来源文件、页码、视觉行号和提取方式。
- 只处理用户明确指定的省份、年份和任务目录，不混合无关任务。

## 推荐流程

1. 日常已验证结构直接运行 `daily`，自动检查环境、复用缓存并导出唯一汇总文件。
2. 查看 Excel 的 `metadata`、`review` 以及可用的汇总校验表。
3. 只有程序因未知结构停止时，才由项目维护者运行 `inventory`、`extract`、`check` 并适配结构家族。

详细命令见 `RUNBOOK.md`。数据库字段标准以 `reference/field_spec.md` 为准。
