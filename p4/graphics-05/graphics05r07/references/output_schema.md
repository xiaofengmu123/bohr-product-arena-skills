# 输出契约与规范字段（canonical schema）

## 1. result.json 顶层

`result.json` 必须含 `case_id`，并**原样回填**题目 `case.json` 中 `output_contract` 的全部键
（缺值给 `null` 或 `[]`，不改名、不删除未知键）。若题目未提供 `output_contract`，使用下表字段。

```json
{
  "case_id": "graphical-abstract-7030fff2",
  "status": "partial",
  "graphical_abstract": { "question": null, "method": null, "results": null, "conclusion": null },
  "editable_source": { "format": "svg", "path": "fig_xxx.svg", "layers": ["layer-title", "..."], "text_editable": true },
  "exports": [ { "format": "png", "path": "fig_xxx.png", "renderer": "rsvg-convert", "status": "rendered" } ],
  "entities": [], "relations": [], "data_mapping": [], "legend": [],
  "sources": [], "uncertainty": [], "change_log": [],
  "limitations": [], "pending_human": [], "errors": [], "degradations": [],
  "runtime": { "skill_version": "1.1.0", "started_at": "...", "finished_at": "..." }
}
```

`status` 只取 `completed | partial | failed`：四要素齐全、导出与溯源无缺 → `completed`；
有缺口但骨架与主图可用 → `partial`；空输入或无法产出任何主产物 → `failed`。

## 2. 字段语义

| 字段 | 含义 | 最低要求 |
|-|-|-|
| `graphical_abstract` | 四要素：question/method/results/conclusion | 每项为短句或 `null`；`null` 时必须出现在 `pending_human` |
| `editable_source` | 分层可编辑 SVG | `format=svg`、真实路径、`layers` 为具名 `layer-*`、`text_editable=true` |
| `exports` | PNG/PDF 预览 | 真实文件；无渲染器时 `status=preview_not_rendered`，禁止伪造 |
| `entities` | 科学实体 | 每项 `{id,type,label,source}`；`source` 含 locator + quote |
| `relations` | 实体关系 | 每项 `{id,from,to,type,label,source}`；`type` 为 `directed|undirected` |
| `data_mapping` | 数值→视觉通道映射 | 每项 `{id,value,unit,visual_channel,target,source}` |
| `legend` | 图例 | 每项 `{label,color,source}`；色块与数据编码一一对应 |
| `sources` | 证据清单 | 每项 `{id,file,locator,quote,role}` |
| `uncertainty` | 不确定项 | 每项 `{id,item,reason,severity}` |
| `change_log` | 修改记录 | 每项 `{step,action,reason,at}`；每次改写递增 |
| `limitations` | 已知限制 | 每项 `{item,severity,at}` |
| `pending_human` | 待人工确认 | 每项 `{id,item,why}` |
| `errors` / `degradations` | 输入解析异常 / 降级记录 | 由脚本自动填充 |

## 3. 证据溯源格式（硬要求）

每个关键结论、数值、图元都要能指回材料中的逐字片段：

```json
{ "locator": "materials/paper.md#p3", "quote": "treated group increased to 45 mg", "role": "results-bar" }
```

- `locator` 用 `文件名#段落/页` 或 `case.json:字段名`；`quote` 为逐字片段（≤240 字符）。
- 冲突信息不隐藏：同一指标两个来源不一致时两个都记录，并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `not_reported`，并把需确认项放入 `pending_human`。

## 4. 自检要点（对应评分维度）

- **结果完整性 / 可核验性**：`case_id` + 全部 contract 字段 + status/sources/uncertainty/change_log。
- **实体关系与数据映射正确**：每个方框/箭头/柱/图例色块 ↔ 一个 entity/relation/data 条目，各带 sources。
- **结果正确性**：箭头方向与来源句一致；数值带单位、比例尺/条件标签齐全。
- **可编辑源文件 / 导出件**：SVG 分层、具名、文字 `<text>` 可编辑；PNG/PDF 为真实文件。
- **版式与视觉层级**：无画布溢出、无 `<8px` 字号、无低对比；灰度/色盲分离已检查。
- **运行稳定性 / 可复核**：同一输入重复运行结构一致；change_log 随改写递增。
- **异常处理**：缺项/歧义入 uncertainty/limitations，绝不编造。
- **鲁棒性**：空输入、坏 JSON、缺参数、超大文件四类用例均优雅降级、退出码 0。
