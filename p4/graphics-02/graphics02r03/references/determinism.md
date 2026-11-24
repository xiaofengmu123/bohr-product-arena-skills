# 确定性契约（Determinism Contract）

本 Skill 的确定性目标：**同一输入 + 同一 `--seed`，在同一 Python/zlib 运行时内两次运行，
SVG/PNG/PDF 与 `*.mapping.json` 逐字节一致；`result.json` 除 `change_log[*].time_utc`
与运行时时间戳字段外逐字节一致。**

## 为什么把确定性做成显式契约

科研图的"可复核、可直接投稿"要求任何一名复核者按相同材料、相同规格、相同 seed 重跑后
得到同一张图。若图元顺序、随机扰动、调色板或坐标计算每次不同，数值→图元的可回溯性就
无从谈起。因此本 Skill 不做随机布局、不做随机抖动、不依赖墙钟或机器路径。

## 保证手段

1. **固定 seed**：`--seed` 缺省 20261122，写入 `result.json` 的 `determinism.seed` 与
   `mapping.json`。
2. **排序稳定**：遍历输入文件、字典键、材料清单、契约字段一律排序后再处理。
3. **规范化 JSON**：`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`，
   输出统一 `\n` 换行、无 BOM。
4. **无环境信息**：SVG/PNG/PDF 不写生成时间、不写绝对路径、不写作者/主机；PNG 不写 tIME，
   PDF 不写 `/CreationDate` 与 `/ID`。
5. **固定格式数字**：刻度与坐标用 `%.6g`/三位小数并去尾零；调色板固定；ID 顺序生成
   （`g_S0_C0`、`cell_R0_C0`…）。
6. **纯标准库渲染**：不依赖 matplotlib 版本，PNG 用 zlib、PDF 用固定对象结构，避免第三方库
   版本差异引入的字节漂移。

## 明确排除（非确定性部分）

- `change_log[*].time_utc` 与 `runtime` 中的墙钟字段：用于审计真实性，必然不同；自检脚本
  `selftest.py` 在比对 `result.json` 时将其归一化。
- zlib 压缩字节：同一 zlib 版本内确定；跨 zlib 大版本理论可能不同。科学内容（图形、数值、
  映射）不受影响。

## 中文/单位文字的处理边界（确定性转写）

- **SVG**：保留原始 Unicode 文本（中文、µ、×、⁻¹ 等），由查看器字体渲染，是"标注的真值"。
- **PDF**：使用 base-14 Helvetica + WinAnsiEncoding，仅支持 Latin-1；非 ASCII 按固定表转写
  （µ→u、μ→u、×→x、⁻→^、³→3、²→2、±→+/-、°→deg、≥→>=、≤→<=），其余→`?`，转写规则
  写入 `uncertainty`。
- **PNG**：纯标准库无字体光栅器，用内置 5×7 位图字库渲染 ASCII（数字/大写/常见符号），
  非 ASCII 同样按上表转写；长标签缩写。完整标注以 SVG 为准，PNG 定位为"数值→图元几何的
  确定性光栅预览"。

## 可复现步骤（评审/他人重跑）

```bash
export INPUT_DIR=/input OUTPUT_DIR=/app/output WORK_DIR=/app/work
python scripts/main.py run --seed 20261122
python scripts/selftest.py
```

数据图规格统一放入 `work/chart_spec.json`（kind=bar|line|scatter|heatmap），字段：

```json
{
  "kind": "bar",
  "title": "标题",
  "x_label": "Treatment",
  "y_label": "Viability (%)",
  "categories": ["Control", "Low", "High"],
  "error_kind": "sd",
  "series": [
    {"name": "24h", "values": [100, 82.3, 61.5], "errors": [2.1, 3.4, 2.9],
     "rows": [2, 3, 4], "cols": [2, 2, 2]}
  ],
  "figure_notes": "Mean ± SD, n=3.",
  "source": {"file": "data.csv", "kind": "csv"},
  "row_offset": 2,
  "col_offset": 2
}
```

`rows`/`cols` 可显式给出每个数值在源表的行列号；缺省按 `row_offset + 类别序号` 与
`col_offset + 系列序号` 推断。`render` 后生成 `figures/<name>.svg|.png|.pdf|.mapping.json|.spec.json`，
其中 `mapping.json` 记录每个图元 id 的 `series/category/row/col/value`，供复核。
