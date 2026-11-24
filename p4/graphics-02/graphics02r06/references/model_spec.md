# 中间表示与可复核规范（本 Skill 的核心设计）

本 Skill 与“直接画图”式方案的区别在于：**LLM 只负责从材料提炼出结构化中间表示，
渲染与导出由确定性脚本完成**。图一旦画成，源数据 → 数据模型 → 图模型 → 场景 → SVG
的链条每一步都可回溯、可 diff、可独立重跑。

## 1. 四层中间表示

| 文件 | 谁写 | 职责 |
|---|---|---|
| `plan.json` | LLM（S1） | 执行计划：实体表、关系表、图面约束、图型决策、样式预设、校验规则 |
| `data_model.json` | LLM（S2） | 源数据层：每个数据单元带 locator + quote + 解析值 + 单位 + issue |
| `figure_model.json` | LLM（S3） | 图模型层：每个图元带 value/几何 + 样式 + `source` 溯源指针 |
| `scene.json` | `render.py` | 规范化场景：数据值已映射为像素几何，供 SVG/PNG/PDF 共用 |

`render.py` 只读 `figure_model.json`，产出确定性场景并渲染；渲染阶段没有 LLM 判断，
因此**同一输入两次运行得到同一张图**（固定坐标、固定调色板、固定布局）。

## 2. 溯源约定（每个关键结论/图元必须有 locator + 逐字 quote）

`source` 对象统一为：

```json
{
  "locator": "/input/materials/fig3_data.csv#row2:col2",
  "quote": "23.5",
  "field": "value",
  "unit": "ng/mL"
}
```

- `locator`：`文件路径#定位`。CSV 用 `#rowN:colM`（1 起）；PDF 用 `#p页码` 或段落定位；
  图片用 `#<figure_id>`。写实际读取到的位置，不臆造。
- `quote`：从源材料**逐字摘录**的原文片段（数值就是数值本身、实体名就是原文拼写）。
- 冲突信息不隐藏：同一指标两处不一致时，两处都记录并标 `source_conflict` 到 `uncertainty`。
- 没有 quote 的图元（装饰性 +、* 等符号）可以不写 `source`；**一旦写了 `source` 就
  必须同时有 `locator` 与 `quote`**，否则 `selftest.py` 报 `evidence_traceability` 失败。

## 3. figure_model.json 模式（渲染端唯一输入）

```json
{
  "case_id": "…",
  "figure_id": "figure03",
  "figure_type": "bar | line | scatter | box | schematic | flowchart | mechanism | composite",
  "canvas": {"width_pt": 360, "height_pt": 300, "background": "#FFFFFF"},
  "font": {"family": "sans-serif", "size_pt": 8},
  "palette": ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2"],
  "panels": [ { … } ],
  "legends": [ {"id": "l1", "bbox": [x0,y0,x1,y1], "entries": [{"label": "WT", "color": "#4C72B0"}]} ],
  "annotations": [ {"id": "sig", "text": "* p<0.05", "at": [x,y], "style": {}} ],
  "caption": "Fig. 3. n=6 per group; error bars, SEM."
}
```

### 数据面板（bar/line/scatter/box）

`panels[].axes` 声明坐标轴；`panels[].series[].marks[]` 声明数据图元，**value 仍是原始
数据值**，由 `render.py` 依 `y.min/max/scale` 做线性或 log10 映射，避免 LLM 手算像素。

```json
{
  "id": "a",
  "bbox": [45, 25, 250, 240],
  "axes": {
    "x": {"title": "Treatment", "min": 0, "max": 2,
          "ticks": [{"label": "Ctrl", "pos": 0.5}, {"label": "Trt", "pos": 1.5}]},
    "y": {"title": "Concentration (ng/mL)", "min": 0, "max": 30,
          "scale": "linear", "ticks": [0, 10, 20, 30]}
  },
  "series": [
    {"name": "WT", "color": "#4C72B0", "marks": [
      {"id": "WT_Ctrl", "x_data": 0.5, "value": 23.5,
       "error": 2.1, "err_type": "SEM", "n": 6, "show_value": true,
       "source": {"locator": "…csv#row2:col2", "quote": "23.5", "field": "value", "unit": "ng/mL"}}
    ]}
  ]
}
```

- 误差线含义：`err_type` ∈ {SD, SEM, CI, n}，`n` 为样本量；图注必须写清含义。
- 箱线图：mark 用 `"stats": {"q1", "median", "q3", "lower", "upper", "outliers": []}`。
- 分组柱：同组用相同 `x_data`，不同系列自动错开；组距由唯一 `x_data` 中位间隔推定。

### 几何面板（schematic/flowchart/mechanism）

`panels[].marks[]` 直接给几何图元，坐标系为 SVG pt（原点左上，y 向下）：

```json
{"id": "node_A", "kind": "rect", "geom": {"x": 20, "y": 60, "w": 90, "h": 40},
 "style": {"fill": "#E8F0FA", "stroke": "#4C72B0", "stroke_width": 1},
 "text": "TF", "source": {"locator": "…pdf#p3", "quote": "transcription factor"}}
{"id": "edge1", "kind": "arrow", "geom": {"x1": 110, "y1": 80, "x2": 170, "y2": 80},
 "style": {"stroke": "#222222", "stroke_width": 1.2}}
```

支持图元：`rect / line / hline / vline / arrow / circle / ellipse / polyline / path / text`。
`text` 的 `geom.text` 为文字内容；箭头方向由 (x1,y1)→(x2,y2) 决定，与源材料的
抑制/激活/上下游关系一致，逐条在 `plan.relationships` 记录。

## 4. data_model.json 模式（源数据层，可复核）

```json
{
  "cells": [
    {"id": "c1", "locator": "/input/materials/fig3_data.csv#row2:col2",
     "quote": "23.5", "raw": "23.5", "parsed": 23.5, "unit": "ng/mL",
     "issue": null}
  ],
  "tables": [ {"name": "fig3_data", "columns": ["group", "WT", "KO"], "row_count": 4} ],
  "conflicts": [ {"a": "c1", "b": "c7", "note": "同一指标两处值不一致"} ]
}
```

`render.py --data` 会校验每个 `figure_model` 图元的 `source.locator` 能否在 data_model
中定位到对应 cell；无法定位 → 记录 issue，不静默通过。

## 5. 双向映射（图 ←→ 数据）

- **图 → 数据**：SVG 每个图元元素带 `id` 与 `data-src` 属性，`<metadata>` 内是完整的
  `id → source` 映射；从图上一个柱子可直接读到其来源 cell 与逐字 quote。
- **数据 → 图**：`scene.json` 与 `figure_model.json` 按 `id` 索引，从 cell 可定位到
  对应的柱/点/色阶/误差线 id。
