# IR 字段、图元与证据溯源规范（scix-figure-contract-skill）

IR 是“材料 → 图稿”之间的结构化中间表示，文件为 `/app/work/ir.json`。Agent 负责抽取科学内容并附证据；脚本负责确定性布图、生成 SVG 与自检。**IR 只承载可从材料追溯到的事实，禁止补造。**

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `title` | string | 是 | 图题，来自材料任务或题面。 |
| `figure_kind` | string | 是 | `apparatus`\|`mechanism`\|`flow`\|`route`\|`chart`。 |
| `canvas` | object | 否 | `{"width": int, "height": int}`，缺省 1200×800。 |
| `meta` | object | 否 | `layout` = `grid`\|`vertical`\|`horizontal`（缺省 grid）；脚本自动写入 `auto_layout`。 |
| `entities` | array | 是 | 科学实体/节点。 |
| `relations` | array | 是 | 空间/流向/顺序关系。 |
| `steps` | array | 否 | 有序步骤（可并建模为带 `step_index` 的实体）。 |
| `legend` | array | 否 | 图例映射。 |
| `annotations` | array | 否 | 附加标注。 |
| `data_encoding` | array | 是 | 数值/参数 → 尺寸、颜色、线宽、编号的映射。 |

## 2. 实体（entities）

```json
{"id": "e1", "label": "冷凝器", "type": "condenser",
 "x": 120, "y": 80, "w": 160, "h": 56,
 "fill": "#eef2f7", "stroke": "#334155",
 "source_ref": "materials/apparatus.pdf#p3",
 "quote": "冷凝器（编号 C1）置于反应釜上方"}
```

- `id`：图内唯一，确定性命名（e1、e2……）；同类部件**不合并**，用 `id` 区分。
- `x/y/w/h`：可省略，脚本按 `meta.layout` 网格确定性补齐；补齐后标 `auto_layout`。
- `type`：供图元规范与配色复用，常用 vessel/pump/valve/condenser/step/stage/material/signal。
- `source_ref` + `quote`：**必填**。`source_ref` 是 locator（文件+页码/段落），`quote` 是材料中逐字片段，证明该实体存在，不是模型脑补。

## 3. 关系（relations）

```json
{"from": "e1", "to": "e2", "kind": "material|energy|info|sequence",
 "direction": "forward|reverse|bidirectional",
 "style": "solid|dashed", "label": "冷凝液回流",
 "color": "#1f2937",
 "source_ref": "materials/apparatus.pdf#p4", "quote": "冷凝液回流至反应釜"}
```

- `from`/`to` 必须指向存在的实体 `id`，否则自检报 `dangling`。
- `direction`：`forward` 箭头在 `to` 端；`reverse` 在 `from` 端；`bidirectional` 两端；回流/废料出口用 `style:"dashed"`。
- `kind` 决定语义：物料、能量、信息、顺序（步骤连线用 `sequence`）。

## 4. 步骤与阶段（steps / route）

流程/技术路线建议把步骤建模为实体，`type:"step"` 或 `type:"stage"`，并用 `step_index` 保持与正文编号一致，再用 `sequence` 关系连线：

```json
{"id": "s1", "label": "1 细胞培养", "type": "step", "step_index": 1,
 "source_ref": "materials/protocol.pdf#p2", "quote": "第一步：细胞培养"}
```

并行支路 = 多条出边；循环回流 = `direction:"reverse"` 或 `style:"dashed"` 回边；终止条件作为 `annotations` 文本。

## 5. 图例与数据编码

- `legend`：`{"key", "visual", "text", "source_ref"}`。`visual` 形如 `"color:#e11d48;dashed"`，与图中线型/颜色**必须一致**。
- `data_encoding`：`{"variable", "visual", "source_ref", "quote"}`，说明哪个数值/参数映射成哪种尺寸、颜色、线宽或编号；数值必须与来源一致。

## 6. 证据溯源硬要求

- 每个 entity/relation/annotation/legend/data_encoding 均带 `source_ref`；能给出逐字片段的一律给 `quote`。
- 同一指标两处来源冲突时，两侧都记录并标 `uncertainty.source_conflict`，不隐藏。
- 无法确认的部件/数值/方向写 `uncertainty` 与 `limitations`，写“无法判断”，**不补造**。
- 生成 SVG 时，`<metadata>` 内嵌 locator→quote 证据表；每个图元带 `data-source-ref`，可定位回材料。
