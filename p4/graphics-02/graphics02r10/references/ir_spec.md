# 中间表示（IR）与溯源约定

本 Skill 的核心设计是**声明式中介表示（IR，`figurespec/1`）**：出图与校验都不直接对着“绘图库调用”，而是对着一份可被复读、可被 diff、可被追溯的 JSON。渲染是 IR 的纯函数，重跑脚本即得到同一张图。

## 1. 为什么用 IR

- **可解释**：每个图元带 `data_ref`（表名#行#列）与 `quote`（源材料逐字片段），图面与源数据双向可查。
- **可复现**：IR 快照了数据、样式、导出参数与源文件哈希；同一 IR 重跑渲染结果一致。
- **校验前置**：先校验 IR（schema → 溯源 → 数值映射），错误在成图前就被拦下，避免“画得像但不准”。
- **降级友好**：没有 matplotlib 时，标准库手写 SVG 仍从同一 IR 出图，只有 PNG/PDF 如实降级。

## 2. IR 结构（关键字段）

```json
{
  "schema": "figurespec/1",
  "figure": {"id": "fig3a", "kind": "bar|line|scatter|box|heatmap|graph", "title": ""},
  "data": {
    "tables": [
      {"name": "t_fig3a",
       "source": {"file": "materials/x.csv", "locator": "file:...", "sha256": "..."},
       "header": ["group","mean","sd"], "rows": [["A","12.5","2.1"]],
       "column_meta": [{"name":"group","dtype":"string","unit":null,"role":"x"}]}
    ],
    "mapping": {"t_fig3a": {"x_column":0,"value_column":1,"error_column":2}}
  },
  "glyphs": [
    {"id":"bar-1","kind":"bar","data_ref":"t_fig3a#r1c2","value":12.5,"quote":"12.5",
     "unit":null,"x_label":"A","series":null,"error":2.1,"error_kind":"SD","color":"#4C72B0"}
  ],
  "axis": {"xlabel":"", "ylabel":"", "ymin":0, "ymax":15},
  "style": {"font_family":"Noto Sans CJK SC", "font_size":11, "palette":[...], "background":"#ffffff"},
  "export": {"size":{"w":800,"h":600}, "png":{"dpi":300,"colorspace":"RGB"}, "pdf":{"embed_fonts":true}},
  "nodes": [...], "edges": [...], "matrix": {...}, "boxes": [...]
}
```

- `glyphs`：数据图的每个图元（柱/点/误差线/箱体/热图格）。
- `nodes` / `edges`：示意图/机制图/流程图的实体与关系（edge.kind 支持 activation / inhibition / binding 等）。
- `boxes`：箱线图五数（min/q1/median/q3/max）。
- `matrix`：热图（row_labels / col_labels / values）。

## 3. 数据到图元的双向映射

- **正向**：源表单元格 → 图元。`data_ref = "<table>#r<row>c<col>"`（1 起），`quote` 为源单元格逐字文本，`value` 为其数值。
- **反向**：图元 → 源表单元格。`scripts/main.py --phase build` 输出 `reverse_index.json`（`data_ref -> [glyph_id]`），SVG 图元带 `data-id` / `data-ref` / `data-value` 属性，可直接在图面上定位来源。
- **冲突不隐藏**：同一指标出现两个来源时，两个都写入 evidence，标注 `source_conflict`，不擅自取一。

## 4. 导出预设（真实渲染，非占位）

| 产物 | 要求 | 检查脚本 |
|-|-|-|
| SVG | 分层 `<g>`，文字为 `<text>` 节点（不转曲），图元带 `data-*` | `check_svg_text()` |
| PNG | ≥300 dpi、RGB、白底无意外透明，魔数 `\x89PNG`，IHDR 可解析、pHYs 可换算 dpi | `png_info()` |
| PDF | 以 `%PDF-` 开头，矢量文字可选、字体嵌入（matplotlib savefig 默认嵌入） | `file_header()` |
| figure_spec.json / evidence_map.json / reverse_index.json | 随结果一并拷贝到 `/app/output/` | `finalize()` |

字体：`discover_cjk_fonts()` 优先取系统中文字体（matplotlib font_manager，可选），找不到则回退英文标签并在 `uncertainty` 记 `cjk_font_missing`。

## 5. 与“直接 extract→plot”方案的区别

1. 校验顺序不同：schema → provenance → mapping（成图前）→ artifact（成图后），而非只在最后检查交付物。
2. 渲染是纯函数：SVG/PNG/PDF 三件套都从同一 IR 生成，消除“源文件与导出件不同步”。
3. 双向溯源：反向索引 + `data-*` 属性，支持“从图回到数据、从数据定位到图”。
