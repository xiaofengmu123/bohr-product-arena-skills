# FigureIR 规范（figure-ir-schema.md）

FigureIR 是本 Skill 的**单一数据源**：实体、关系、图例、标注、证据全部写进
`/app/work/figure_ir.json`，脚本据此确定性渲染 SVG 并派生 `result.json` 的
provenance/sources。图、数据、证据三者同源，改数据重跑脚本即重绘。

## 1. 顶层结构

```json
{
  "meta": {...},
  "entities": [...],
  "relations": [...],
  "groups": [...],
  "legend": [...],
  "annotations": [...]
}
```

所有数组可为空；但图例/关系为空会触发 `validate_ir` 的提示（写入 uncertainty）。

## 2. meta

| 字段 | 必填 | 说明 |
|-|-|-|
| `case_id` | 是 | 与 `/input/case.json` 的 case_id 一致 |
| `figure_type` | 是 | `mechanism` / `flow` / `hierarchy` / `chart` |
| `title` | 否 | 图标题（可留空） |
| `canvas` | 否 | `{width, height, dpi, font_family}`；缺省 1600×1100@300dpi |
| `auto_legend` | 否 | 保留位；当前图例一律取自 `legend` 数组并做一致性校验 |

## 3. entities

```json
{"id": "e1", "label": "RAS", "kind": "protein", "group": "g1",
 "attributes": {"unit": "µM", "value": "10"},
 "evidence": {"source_ref": "paper.pdf", "locator": "p3 左栏", "quote": "RAS is activated", "confidence": "high", "version": "v1", "confirmed": true}}
```

- `id`：图内唯一，SVG 中作为 `data-entity` 与 `id="node-<id>"`，可复核。
- `kind`：建议受控值 `protein|complex|process|condition|group|other`，仅作提示不硬校验。
- `group`：所属分组 id（对应 `groups`），用于包含关系渲染。
- `attributes`：数值与单位，**必须来自材料**；图中不画材料不存在的数值。
- `evidence`：见 §7。无原文引用时 `quote=""`、`confidence="agent_inferred"`、`confirmed=false`。

## 4. relations

```json
{"id": "r1", "from": "A", "to": "B", "type": "activates", "label": "激活",
 "color": "#333333", "bidirectional": false,
 "evidence": {...}}
```

- `type` 受控词（硬校验）：

| type | 图形语法 | 典型含义 |
|-|-|-|
| `activates` | 实线 + 三角箭头 | 激活/促进 |
| `inhibits` | 实线 + 垂直短杠（tee） | 抑制/阻断 |
| `causes` / `leads_to` / `flows_to` | 实线 + 三角箭头 | 因果/流程方向 |
| `contains` / `part_of` | 实线 + 箭头 | 包含/组成 |
| `temporal` | 实线 + 箭头 | 时序先后 |
| `parallel` | 两端箭头或并列标记 | 并列/互作 |

- 方向由 `from`→`to` 决定；**上下游不可倒置，抑制不得画成箭头激活**。
- `color` 缺省 `#333333`；不同含义使用不同颜色时，必须与 `legend` 对应。

## 5. groups

```json
{"id": "g1", "label": "胞膜", "kind": "container", "members": ["e1","e2"], "evidence": {...}}
```

- 渲染为虚线容器框，成员放在框内；`members` 未列全时，实体上的 `group` 字段也会并入。

## 6. legend（与数据同源，交叉校验）

```json
{"item_id": "lg1", "label": "激活", "color": "#333333", "marker": "arrow",
 "maps_to": "relation.type", "maps_value": "activates", "evidence": {...}}
```

- `maps_to`：`relation.type` 或 `entity.kind`（`relation.type` 自动归一为 `relations.type`）。
- `maps_value`：该图例项对应的实际取值；脚本会校验「图例值 ∈ 数据字段的实际取值集合」，并检查「每个实际取值都有对应图例项」。颜色重复且含义不同会告警。
- `marker`：`arrow|tee|line|shape`，决定图例样本的形状。

## 7. evidence（证据溯源，硬要求）

每条实体/关系/图例/标注都应带 evidence：

| 字段 | 说明 |
|-|-|
| `source_ref` | 材料引用名（如 `paper.pdf`、`table1.csv`） |
| `locator` | 定位（页码/节/行列/图区），如 `p3 左栏`、`Sheet1!A2:B5` |
| `quote` | **逐字引用**的原文片段；无原文引用填 `""` |
| `confidence` | `high|medium|low|agent_inferred` |
| `version` | 版本号，缺省 `v1` |
| `confirmed` | 布尔；未显式设置时按「是否有 quote」推断 |

`result.json` 的 `provenance` 由 evidence 直接派生（element/source_ref/locator/quote/mapping/version/confirmed），`sources` 由 evidence 去重聚合。

## 8. annotations

```json
{"id": "a1", "text": "10 µM, 24h", "target": "e1", "unit": "µM",
 "evidence": {...}}
```

标注可带单位；数值与单位必须可追溯到材料。

## 9. 最小可用流程

1. `python scripts/main.py ...` 落骨架并生成 `work/figure_ir.template.json`。
2. 读材料，把实体/关系/图例/证据填进 `work/figure_ir.json`。
3. 重跑 `main.py`：schema 校验 → 证据完整性 → 图例一致性 → 渲染 SVG → 导出自检。
4. `python scripts/selftest.py` 验证工具链本身。
