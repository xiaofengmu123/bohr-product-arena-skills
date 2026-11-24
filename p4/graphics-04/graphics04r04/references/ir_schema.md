# IR 字段、证据/置信度模型与布图规范（scix-evidence-figure-skill）

## 1. IR 顶层字段

`/app/work/ir.json` 是“材料 → 图稿”之间的唯一中间表示，由 Agent 从材料抽取，脚本据此确定性生成 SVG。字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json` |
| `kind` | string | `apparatus` / `process_flow` / `mechanism` / `technical_route` / `chart_edit` |
| `title` | string | 图题，可空 |
| `entities` | list | 科学实体（部件/单元/阶段/里程碑） |
| `relations` | list | 空间/流向/顺序关系 |
| `data_encoding` | list | 数值/参数 → 尺寸、颜色、线宽、编号的映射 |
| `annotations` | list | 图面标注文字 |
| `legend` | list | 图例映射（可缺省，脚本从 relations 自动生成） |

## 2. 证据与置信度模型（本版核心）

每个图元必须带：

```json
{
  "evidence": {"locator": "materials/paper.md#p3", "quote": "逐字原文片段", "status": "verbatim"},
  "confidence": "high"
}
```

- `status`：`verbatim`（逐字）/ `paraphrase`（转述）/ `inferred`（推断）/ `missing`（缺失）/ `conflicting`（冲突）/ `not_reported`（未报告）。
- `confidence`：`high` / `medium` / `low`。
- 硬规则：`status ∈ {missing, not_reported}` 或 `confidence=low` 的图元，渲染为虚线边框并标“?”，**不得写成既定事实**；其说明写入 result.json 的 `uncertainty` 或 `pending_human`。
- 冲突：同一指标两处来源不一致时，两条都记录并标 `source_conflict`，不隐藏。

## 3. entity 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一，如 `e1` |
| `label` | string | 图面显示名，必须与材料一致 |
| `type` | string | 见下“图元规范”的类型表，决定填充色 |
| `role` | string | 可选，如 `source`/`sink`/`stage` |
| `x`,`y` | number | 可选；缺省用确定性网格布局 |
| `evidence`,`confidence` | — | 见上 |
| `note` | string | 可选，低置信时的说明 |

## 4. relation 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 唯一，如 `r1` |
| `from`,`to` | string | 指向存在的 entity.id，无断头 |
| `kind` | string | `flow`/`material_flow`/`connect`/`sequence`/`dependency`/`return`/`feedback`/`energy_flow`/`info_flow` |
| `direction` | string | `forward`（箭头在 to）/ `backward`（箭头在 from，回流）/ `bidirectional` |
| `label` | string | 可选，关系旁标注 |
| `evidence`,`confidence` | — | 见上 |

约定：回流/循环/反馈用 `return`/`feedback`（红色虚线）；物料流向用 `flow`/`material_flow`（实线箭头）。

## 5. 布图规范

- 确定性网格：`process_flow`/`technical_route`/`mechanism` 单列纵向（体现步骤顺序）；`apparatus` 按 `ceil(sqrt(n))` 列网格。
- 顺序以 IR 中 entity 出现顺序为准，不随机；`x/y` 显式给出时覆盖网格位置。
- 画布按实体包围盒 + 图例高度自适应，避免溢出裁剪。
- 箭头端点在实体边框上截断，方向由 `direction` 决定；自环渲染为小弯弧并记为 warning。

## 6. 图元规范（类型 → 填充色，确定性）

vessel/reactor/tank/column → 浅蓝；source/reservoir/feed → 浅绿；sink/waste/outlet/product → 浅红；instrument/sensor/gauge → 浅黄；pump → 浅靛；valve → 浅紫；filter → 浅粉；mixer → 浅青；stage/milestone/step/decision → 流程色；default → 浅灰。具体色值见 `scripts/core_impl.py` 的 `TYPE_FILL`。

## 7. 与 result.json 的对应

- `ir.entities` → `result.json.entities`
- `ir.relations` → `result.json.relations`
- `ir.data_encoding` → `result.json.data_encoding`
- 图例由脚本从 relations/entity types 生成 → `result.json.legend_annotations`
- SVG 文件 → `result.json.editable_sources`；PNG/PDF → `result.json.exports`
