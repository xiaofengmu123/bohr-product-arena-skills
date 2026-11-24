# figure_model.json：中间表示规范（v1.0）

本 Skill 的核心设计：**所有科学内容先沉淀为一份结构化中间表示 `figure_model.json`，再由此确定性渲染出分层 SVG、矢量 PDF 与 PNG**。图面元素、关系方向、图例与来源溯源都写在这份模型里，因此“改数据→改模型→重跑 `render.py`”即可同步重绘，无需重写绘图代码；审稿人也能逐项复核每个元素指回材料的位置。

## 1. 设计原则

1. **单一事实源**：`figure_model.json` 是唯一权威来源；SVG/PDF/PNG 都是它的派生件。
2. **可溯源**：每个 entity / relation / legend item 都可携带 `provenance`（`source_ref` + `locator` + `quote`）。
3. **可复现**：布局是确定性的（按 id 排序、无随机量）；同一模型两次渲染得到字节一致的文件。
4. **可编辑**：SVG 内文字是真实 `<text>`，图层是 `<g>`，字体族可替换；模型改标签后重渲染即可。
5. **不编造**：来源材料里没有的基因名、浓度、时间点、单位一律不写进模型；不确定的写 `uncertainty`，或标注为 `推测`。

## 2. 顶层结构

```json
{
  "schema_version": "1.0",
  "figure": {"id": "figure01", "title": "", "kind": "relationship_schematic",
             "canvas": {"width_px": 2400, "height_px": 1400, "dpi": 300},
             "layers": [{"id": "background", "label": "背景与分组", "z": 0}]},
  "groups": [],
  "entities": [],
  "relations": [],
  "legend": {"items": [], "data_mapping": null},
  "annotations": [],
  "units": [],
  "panels": [],
  "chart": null,
  "uncertainty": [],
  "notes": []
}
```

- `figure.kind`：`relationship_schematic`（关系示意图，默认）、`mechanism`（机制图）、`flowchart`（流程图）、`concept`（概念图）、`chart`（数据图表）。前四类走节点-关系渲染；`chart` 走数据图表渲染。
- `figure.layers`：定义渲染图层及 z 序；渲染器保证 `<g id="layer-…">` 顺序一致。
- `canvas`：画布尺寸与导出 dpi；`width_px/height_px` 为导出分辨率目标。

## 3. groups（分组/层级）

```json
{"id": "g1", "label": "细胞核", "contains": ["e1", "e2"],
 "provenance": {"source_ref": "", "locator": "", "quote": ""}}
```

`contains` 列出成员实体 id。渲染器自动把分组画成包围成员的圆角框，成员在框内。**包含关系用 group，不用 relation**，避免“包含画成并列”。

## 4. entities（实体）

```json
{"id": "e1", "label": "Gene A", "type": "gene", "group": "g1",
 "style": {"fill": "#E3F2FD", "stroke": "#1565C0", "shape": "rounded",
           "font_family": "sans-serif", "font_size": 22},
 "layout": {"row": 0, "col": 0},
 "provenance": {"source_ref": "materials/paper.pdf#p3", "locator": "第3页结果段",
                "quote": "……Gene A 表达上调……", "confirmed": true}}
```

- `type`：`gene | protein | complex | process | phenotype | condition | timepoint | value | annotation | other`。类型仅影响语义记录，不改变布局。
- `style.shape`：`box | rounded | ellipse | circle | hexagon | plain`（plain 只画文字不画边框）。
- `layout`：`{row, col}` 网格提示或 `{x, y}` 绝对坐标。**全部实体都给出坐标时按坐标布局，否则用确定性自动分层布局**（有向关系定层级，组内按 id 排序）。
- `provenance.confirmed`：`true`（已核对）/ `false`（推测）。

## 5. relations（关系）

```json
{"id": "r1", "from": "e1", "to": "e2", "kind": "activation",
 "directed": true, "label": "促进", "style": {"line": "solid"},
 "provenance": {"source_ref": "", "locator": "", "quote": ""}}
```

- `kind`：`activation`（激活，实线箭头）、`inhibition`（抑制，实线 T 形端）、`binding`（结合，两端点）、`parallel`（并列，无箭头）、`upstream_downstream`（上下游，箭头）、`causal`（因果，箭头）、`temporal`（时序，箭头）、`mapping`（映射，虚线）、`containment`（包含：**改用 group，不要用 relation**）。
- 箭头方向始终 `from → to`；**上下游、因果、时序的方向必须与来源一致，不倒置**。
- 同 id 对多条关系：渲染器按序号做轻微垂直偏移，避免重叠。

## 6. legend（图例）与 data_mapping（数据映射）

```json
{"legend": {
  "items": [
    {"id": "lg1", "label": "激活", "color": "#1565C0", "marker": "arrow", "maps_to": ["r1"]},
    {"id": "lg2", "label": "治疗组", "color": "#D32F2F", "marker": "box", "maps_to": ["e3", "e4"]}
  ],
  "data_mapping": {
    "driven_by": "materials/data_table.csv",
    "categories": ["Ctrl", "Treat"],
    "colors": {"Ctrl": "#455A64", "Treat": "#D32F2F"},
    "markers": {"Ctrl": "circle", "Treat": "square"}
  }
}}
```

- `marker` ∈ `arrow | tee | box | ellipse | circle | hexagon | line | dot | none`。
- **图例与实体同源**：`maps_to` 指向真实 entity/relation id；数据驱动图例时 `colors/markers` 的键必须与 `categories` 完全一致，分类数、颜色数、标记数三者相等，否则自检报错。

## 7. chart（数据图表，仅 `kind="chart"`）

```json
{"chart": {
  "type": "bar",
  "x": {"label": "时间", "unit": "h", "values": [0, 6, 12, 24]},
  "series": [
    {"id": "s1", "label": "Ctrl", "color": "#455A64", "marker": "circle",
     "values": [1.0, 1.1, 1.2, 1.15],
     "error": [0.1, 0.1, 0.15, 0.12]}
  ],
  "y": {"label": "相对表达", "unit": "a.u."},
  "provenance": {"source_ref": "materials/data_table.csv", "locator": "Sheet1", "quote": ""}
}}
```

- `type`：`bar | line | scatter`；`series[].error` 可选（画误差棒）。
- 所有数值、单位来自来源材料；图表不承担统计检验，只按给定数据绘图。

## 8. annotations / units / panels

- `annotations`：`{"id":"a1","text":"…","anchor":"e1","place":"top|bottom|left|right"}`。
- `units`：`{"id":"u1","value":"ng/mL","applies_to":["e5"],"source_ref":""}`，渲染到相关值实体旁或图注区。
- `panels`：`{"id":"A","label":"A","x":0,"y":0,"w":800,"h":600}`，用于子图分区与子图编号。

## 9. 校验要点（selftest.py 会执行）

1. `schema_version` 为 `1.0`，entities/relations 的 id 全局唯一。
2. 每条 relation 的 `from/to` 都在 entities 里；group 的 `contains` 都在 entities 里。
3. legend 的 `maps_to` 指向存在的 entity/relation id。
4. 数据映射的分类数 = 颜色键数 = 标记键数，且键集合一致。
5. `provenance` 逐条具备 `source_ref`；缺 `quote` 会提示补齐或显式标 `pending_human`。
6. 渲染产物 SVG 可被 XML 解析、含 `<g>` 图层与 `<text>`；PDF 以 `%PDF-` 开头；PNG 头为 `\x89PNG`。
