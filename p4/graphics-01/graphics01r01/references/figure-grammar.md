# 图稿规范与证据映射（figure-grammar）

本文件定义两个 JSON 的字段：`figure_spec.json`（驱动 SVG 渲染）与
`evidence_map.json`（结论级溯源）。二者通过 `evidence_id` 互指，保证
「图面元素 ↔ 科学结论 ↔ 材料片段」三方可核验。脚本实现见
`../scripts/core_impl.py`，渲染入口 `../scripts/main.py render`。

## 1. figure_spec.json

```json
{
  "title": "机制图标题（可空）",
  "canvas": {"width": 1600, "height": 1000},
  "font_family": "DejaVu Sans, Arial, sans-serif",
  "background": "#ffffff",
  "entities": [
    {
      "id": "n1",
      "label": "实体标签（必须逐字来自材料或为材料同义改写并标注）",
      "kind": "node | group",
      "shape": "rect | roundrect | ellipse | hexagon",
      "x": 100, "y": 100, "w": 160, "h": 48,
      "fill": "#e8f0fb", "stroke": "#333333", "stroke_width": 1.2,
      "font_size": 12,
      "container": "可空；为 group 的 id 表示包含关系",
      "evidence_id": "ev_001",
      "source_ref": "paper.pdf"
    }
  ],
  "relations": [
    {
      "id": "r1",
      "from": "n1", "to": "n2",
      "type": "activates | inhibits | contains | parallel | upstream | causal | temporal | maps_to",
      "label": "边标签（可空）",
      "color": "#333333", "width": 1.4, "dashed": false,
      "evidence_id": "ev_002",
      "source_ref": "paper.pdf"
    }
  ],
  "legend": {
    "title": "Legend",
    "x": 1340, "y": 20, "w": 240,
    "evidence_id": "ev_010", "source_ref": "table1.csv",
    "items": [
      {"label": "类别名", "color": "#4c72b0", "marker": "rect | circle",
       "evidence_id": "ev_011"}
    ]
  },
  "charts": [
    {
      "id": "c1", "type": "bar",
      "x": 600, "y": 420, "w": 520, "h": 340,
      "title": "可空", "x_label": "分组", "y_label": "表达量", "unit": "a.u.",
      "evidence_id": "ev_020", "source_ref": "data.csv",
      "series": [
        {"label": "系列名", "color": "#4c72b0",
         "data": [["Ctrl", 1.0], ["Treat", 2.3]]}
      ]
    }
  ],
  "annotations": [
    {"id": "a1", "text": "标注文字", "x": 100, "y": 60, "font_size": 11,
     "color": "#333333", "evidence_id": "ev_030", "source_ref": "paper.pdf"}
  ]
}
```

关键约定：

- **每个 entity / relation / legend item / chart / annotation 都应有
  `evidence_id`**；渲染器会把它写成 SVG 的 `data-evidence-id` 属性，
  使图元可机器复核。`source_ref` 写入 `data-source-ref`。
- `kind="group"` 的实体是分组容器，其 `container` 为空；被包含实体把
  `container` 指向该 group 的 id，渲染时画虚线圆角框表包含关系。
- 关系 `type` 决定端点样式：`activates/upstream/causal/temporal/maps_to`
  画箭头；`inhibits/suppresses/blocks` 画 T 形抑制杠；`parallel/associates`
  画无箭头连线；`contains` 用嵌套 group 表达，不画边。
- 坐标以像素为单位；缺坐标时不做自动布局（保持确定性，不猜位置），
  由 Agent 按图面避让原则显式给定。
- 图例 `items` 与数据分类同源：改 `legend.items` 或 `chart.series` 后重跑
  `render` 即重绘，实现「数据表驱动图例重绘」。

## 2. evidence_map.json（结论级溯源）

每条结论一个对象；`maps_to` 指向图元 id，`locator` + `quote` 为硬性字段。

```json
[
  {
    "id": "ev_001",
    "claim_kind": "entity | relation | attribute | data | legend | annotation | unit | axis",
    "claim": "一句话科学结论",
    "source_ref": "paper.pdf",
    "locator": {"page": 3, "section": "Results", "anchor": "para 2"},
    "quote": "材料中的逐字原文片段",
    "status": "confirmed | inferred | pending_human | conflict | not_reported",
    "basis": "text | table | figure | ocr",
    "maps_to": ["n1"],
    "notes": "推理说明或冲突说明"
  }
]
```

`status` 语义：

| 状态 | 含义 |
|---|---|
| `confirmed` | quote 逐字支持 claim |
| `inferred` | claim 由材料合理推断，quote 给出依据，须在 notes 写明推理 |
| `pending_human` | 无法判断，须人工确认，同步写入 result.json 的 pending_human |
| `conflict` | 两个来源不一致，两个都记录并在 notes 标注 source_conflict |
| `not_reported` | 材料未记载（用于「无浓度/无时间点」的显式负向核验） |

## 3. 溯源回填规则

- 实体标签若非逐字引用而采用同义改写，`quote` 仍须给原文，`notes` 注明
  「改写自：…」；禁止凭空添加材料不存在的基因名、浓度、时间点、单位。
- 关系方向以材料动词为准：抑制/阻断/降低 → `inhibits`；激活/促进/诱导 →
  `activates`；上下游与因果不能倒置。
- 图例与数值：每个图例项、每条数据都要有对应 evidence，quote 定位到数据表
  单元格或文本，保证「图例-配色-数值」逐项一致。
- 冲突不隐藏：同一指标两处不一致时，两条 evidence 均保留并置 `conflict`。
