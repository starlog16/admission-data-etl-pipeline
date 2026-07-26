---
name: pdf-to-excel
description: 将用户指定的 input/raw 相对目录中的招生图片、文本 PDF、扫描 PDF 或原始 XLSX，通过本项目已验证流水线生成唯一汇总 Excel。用户输入 /pdf-to-excel 路径时使用。
---

# 一键生成汇总 Excel

目标目录：`$ARGUMENTS`

如果参数为空，只询问 `input/raw` 下的相对目录，然后停止。

参数存在时，只执行下面一条命令，不调用任何其他工具：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_pipeline.py daily --input "$ARGUMENTS"
```

禁止读取或修改项目源码、配置和源文件；禁止 Plan、Explore、子代理、worktree、临时脚本、`python -c`、重复 OCR、安装依赖和自行排错。

命令成功时，用不超过 6 行汇报：文件数、新提取数、缓存命中数、记录数、待复核数、Excel 路径。命令返回 `stopped` 时原样汇报原因并停止。
