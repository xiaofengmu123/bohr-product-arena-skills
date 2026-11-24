# IR 与证据溯源规范（scix-figure-evidence）

本文定义结构化中间表示（IR）的字段、图元映射与**证据溯源要求**。核心约定：每一个科学元素（实体、关系、步骤、阶段、数据映射、图例、标注）都必须携带 `source_ref`（定位符）与 `quote`（逐字原文片段）；无法溯源的推导项必须显式标注为 derived，不得伪装成原文。

## 1. IR 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | string | 图标题 |
| `kind` | string | `apparatus` 装置 / `flow` 流程 / `mechanism` 机制 / `route` 技术路线 |
| `confidence` | string | 整体置信度 `high|medium|low`（自动提取为 low） |
| `entities` | list | 科学实体 |
| `relations` | list | 空间/流向/顺序关系 |
| `steps` | list | 编号步骤（流程） |
| `stages` | list | 阶段与里程碑（技术路线） |
| `data_encoding` | list | 数值→图面映射 |
| `legend_annotations` | list | 图例与标注映射 |
| `annotations` | list | 独立文字标注 |
| `layout` | object | 画布与坐标（缺省由脚本确定性布图，标记 `auto=true`） |

## 2. 通用证据字段（每条元素都必须有）

- `source_ref`：定位符，形如 `materials/protocol.md#L12` 或 `case.json`（相对 `/input` 的路径 + 行号）。
- `quote`：逐字原文片段，取输入材料的原句，≤240 字符，超出加 `…`；**禁止改写或意译**。
- `confidence`：`high|medium|low`；`low` 表示自动提取或未确认。
- `method`（可选）：`manual_annotation`（人工/模型确认）、`derived`（推导）、`fallback`（脚本自动）。

无法提供 `source_ref`+`quote` 的项：`source_ref` 与 `quote` 写 `null`，`method="derived"`，并在 `note` 说明理由。

## 3. 各集合字段

### entities
`{id, label, type, source_ref, quote, confidence, note}`
`type`：`device|material|energy|info|port|stage|milestone|metric`。

### relations
`{id, from, to, kind, direction, style, label, source_ref, quote, confidence}`
- `from`/`to` 必须指向存在的实体 `id`（否则自检报 critical）。
- `kind`：`assembly|material_flow|energy_flow|info_flow|sequence|dependency`。
- `direction`：`forward|backward|bidirectional`；回流用 `style="dashed"`。

### steps / stages
`{id, order, label, kind, source_ref, quote, confidence}`
`steps.kind`：`main|parallel|loop|terminal`；`stages` 另加 `goal|method|output`（可为 null）。

### data_encoding
`{id, key, value, maps_to, encoding, legend_label, source_ref, quote, confidence}`
例：`{"key":"flow_rate","value":"5 mL/min","maps_to":"line_width","encoding":"width=2.0px","legend_label":"流量 5 mL/min"}`。

### legend_annotations
`{id, key, meaning, visual, source_ref, quote, confidence, note}`
`key` 采用 `solid_arrow|dashed_arrow|dotted_arrow` 或任意自定义键；`visual` 描述颜色/线型/符号。

### annotations
`{id, text, target, x, y, source_ref, quote, confidence}`

## 4. evidence_map 结构（比扁平的 source 列表更细）

```json
{
  "schema": "evidence_map/v1",
  "elements": {
    "e1": {"collection":"entities","id":"e1","locator":"...","quote":"...",
           "confidence":"high","method":"manual_annotation","label":"反应釜"}
  },
  "reverse_index": {"materials/p.md#L12": ["e1","r1"]},
  "coverage": {"total_elements":10,"quoted_elements":8,"unquoted_elements":2,
               "quote_coverage":0.8},
  "unquoted": [{"collection":"relations","id":"r9","reason":"derived"}]
}
```

- 正向索引 `elements`：元素 → 证据。
- 反向索引 `reverse_index`：来源 → 该来源引出的全部元素，支持"按来源复核"。
- `coverage.quote_coverage`：可追溯比例，直接写进 result.json，便于评审判断可核验性。
- `unquoted`：显式列出无法逐字溯源的元素及原因，**不隐藏、不补造**。

## 5. SVG 图元映射与图层

| 图层 id | 内容 |
|---|---|
| `layer-title` | 标题 |
| `layer-entities` | 实体（矩形/椭圆 + 文字） |
| `layer-relations` | 连线 + 箭头 + 关系标签 |
| `layer-steps` | 步骤/阶段框 |
| `layer-annotations` | 独立标注 |
| `layer-legend` | 图例 |

每个图元写 `data-source-ref`（定位符）、`data-quote`（截断原文）、`data-evidence-id`（指向 evidence_map）与 `data-confidence`；完整 evidence_map 以 `<script type="application/json" id="scix-evidence-map">` 嵌入 `<metadata>`，评审无需另找文件即可复核。

## 6. 确定性要求

- 缺省布局用网格/链式，无随机数；同一输入两次运行产出逐字节一致的结果。
- 坐标冲突时以 `layout.positions` 显式坐标优先，其次脚本确定性布图。
- 文本统一按 `utf-8-sig`→`utf-8`→`gb18030`→`latin-1` 顺序尝试解码；坏编码降级为 replace 并记录 `decoding_errors`。
