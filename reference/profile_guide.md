# 版式 YAML 配置说明

每种“数据结构家族”对应 `config/profiles/` 中一个 YAML，不按省份逐个建立。调整 YAML 不会使已有 OCR 缓存失效。

文本 PDF 和原始 XLSX 优先使用 `structured_table`，直接读取表格单元格；扫描件或图片再使用坐标解析器和 OCR。只有真正新的层级或字段体系才新增结构家族。

## 已验证的双栏计划余额版式

`two-column-plan-balance-score.yaml` 用于左右双栏、按“院校专业组 → 专业”展开，并同时给出计划余额与分数线的扫描 PDF 或图片。该版式会：

- 分别隔离左右栏，防止右栏专业误挂到左栏院校；
- 识别跨行院校专业组标题；
- 恢复 OCR 将“专业组代码”和“专业组计划数”黏连的情况；
- 对缺失的计划数、分数线执行局部单元格 OCR；
- 将无法交叉验证的记录放入 `review`，而不是静默写入 `data`。

## 匹配

```yaml
match:
  path_contains: [anhui/2026]
  filename_contains: [最低分, 名次]
```

两组条件均为“全部包含”。优先级较高的版式先匹配。

## 列边界

`x_min` 和 `x_max` 是页面宽度的比例，范围为 0 到 1：

```yaml
columns:
  - name: 院校代码
    x_min: 0.00
    x_max: 0.10
    type: code
```

支持的基础类型：`text`、`code`、`integer`。

## 复核规则

- `required`：为空时标记待复核；
- `minimum_populated_fields`：一行至少命中多少列才算数据行；
- `skip_if_contains`：标题或表头含这些文字时跳过；
- `fill_down`：将父级字段向下填充；
- `review_all: true`：未验证的通用版式全部进入复核。
