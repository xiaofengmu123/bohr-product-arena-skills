# result.json 输出契约（output_contract）

运行结束时 `/app/output/result.json` 必须是一份可解析的 UTF-8 JSON 对象。缺值写 `null` / `[]`，不得改名或删除题目要求的键。下面按字段定义；`*` 表示必填。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` * | string\|null | 取自 `/input/case.json` 的 `case_id`/`id`/`challenge_id`；找不到写 `null` 并在 `errors` 说明 |
| `task_type` * | string | 固定 `biological_mechanism_pathway` |
| `status` * | enum | `success` \| `partial` \| `insufficient_evidence` \| `input_error` \| `failed` |
| `editable_source_files` * | array | 分层可编辑源文件，见下 |
| `export_artifacts` * | array | 真实导出件（PNG/PDF/SVG 拷贝），见下 |
| `entities` * | array | 结构化实体规格（与 spec.json 一致，含 source） |
| `relations` * | array | 结构化关系规格（type/direction/evidence/modifier/source） |
| `legend` * | array | 图例项，由 `build_svg.legend_entries()` 生成，保证与图面一致 |
| `data_mappings` * | array | 视觉通道→数据字段映射（channel/field/scale/source） |
| `sources` * | array | 聚合证据：`{element_id, locator, quote}` |
| `uncertainty` * | array | 歧义实体、方向不明、无来源数值：`{item, reason, resolution}` |
| `pending_human` * | array | 需人工确认项：`{item, why, suggested_action}` |
| `change_log` * | array | 增量修改记录：`{at, action, reason}`；首条必须是 3 分钟内骨架落盘 |
| `limitations` * | array | `status != success` 时必填，逐条说明缺口 |
| `errors` * | array | 已捕获并处理的错误：`{stage, message, handled:true}` |
| `degradations` * | array | 降级处置：`{scenario, action}` |
| `runtime` * | object | `{started_at, finished_at, seconds, model_requests, svg_bytes}` |

## editable_source_files 元素

`{path, format, description, layers[], editable:true}`。`path` 相对 `/app/output/` 或绝对路径；必须真实存在。SVG 为命名图层（`layer-compartments`/`layer-edges`/`layer-nodes`/`layer-labels`/`layer-legend`），可用 Inkscape/文本编辑器改标签后重导出。

## export_artifacts 元素

`{path, format, width_px, height_px, dpi, font_check, color_check, renderer}`。PNG/PDF 必须由平台工具真实渲染（browser-automation / office-renderer / latex-compiler）。工具缺失时保留 SVG，缺失格式在 `limitations` 说明，**不得伪造 PNG/PDF 记录**。

## 证据溯源（sources / 逐字 quote）

每个关键实体/关系/数值必须能指回材料：`locator` 形如 `materials/paper.md#p3` 或 `/input/case.json`，`quote` 为材料中的逐字片段。冲突信息两条都记录并标 `source_conflict`。无来源写 `not_reported` 并进入 `uncertainty`/`pending_human`，不编造引用、DOI、数值。

## status 判定

- `success`：实体/关系/图例/数据映射齐备，SVG 与至少一种真实导出件存在，契约检查无 error。
- `partial`：有部分产物，但导出未完成或存在未解决缺口（`limitations` 必填）。
- `insufficient_evidence`：材料不足以确定实体/关系（空输入或材料缺失）。
- `input_error`：输入无法解析（case.json 坏 JSON/编码损坏）。
- `failed`：无法写出任何产物。

## 脚本退出码约定

`main.py` / `build_svg.py`：`0` 成功；`2` 仅警告（已产出合法结果）；`1` 阻断错误；`3` 意外崩溃（已尽力写应急 partial 结果）。`selftest.py`：`0` 全部通过，`1` 存在失败。
