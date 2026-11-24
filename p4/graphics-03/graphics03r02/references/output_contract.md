# result.json 输出契约（output_contract）

本文件定义 Skill 在每个案例上必须写入 `/app/output/result.json` 的全部字段。校验脚本为
`scripts/main.py check`（对应 `scripts/check_result.py` 的职责）。所有字段均回填，缺值用
`null` / `[]`，不改名、不删键、不编造。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `schema_version` | string | 是 | 本契约版本 `1.0.0` |
| `case_id` | string | 是 | 取自 `/input/case.json`；读不到用 `"unknown"` |
| `title` | string | 否 | 案例/图标题，取自 `case.json` 或 `task.md` 首行 |
| `status` | string | 是 | `success` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `editable_source_files` | array | 是 | 可编辑分层源文件清单（SVG） |
| `export_artifacts` | array | 是 | PNG/PDF 等导出件清单 |
| `entities` | array | 是 | 结构化实体规格（与 `spec.json` 一致） |
| `relations` | array | 是 | 结构化关系规格（与 `spec.json` 一致） |
| `legend` | object | 是 | 图例依据与说明 |
| `data_mappings` | array | 是 | 颜色/线宽/形状/大小 → 数据的映射 |
| `sources` | array | 是 | 聚合来源定位（元素 → 材料位置 + 逐字 quote） |
| `uncertainty` | array | 是 | 歧义实体、方向不明、无来源数值 |
| `pending_human` | array | 是 | 需要人工确认的事项 |
| `change_log` | array | 是 | 增量修改记录，每条含 `at` 与 `action` |
| `limitations` | array | 条件必填 | `status` 非 `success` 时必须非空 |
| `errors` | array | 否 | 运行期错误（不致命） |
| `degradations` | array | 否 | 降级项（缺数据/缺软件等） |
| `runtime` | object | 否 | `started_at`、`seconds` 等运行信息 |

## editable_source_files / export_artifacts 条目

- 源文件条目：`{"path": "mechanism.svg", "format": "svg", "description": "...", "layers": [...]}`
- 导出件条目：`{"path": "mechanism.png", "format": "png", "width_px": ..., "height_px": ...,
  "dpi": ..., "font_check": "...", "color_check": "...", "generated_by": "..."}`
- `path` 相对 `/app/output/`；`check` 命令会验证文件真实存在，并校验
  PNG 魔数（`\x89PNG`）、PDF 魔数（`%PDF-`）、SVG 可被 XML 解析。
- 无法真实生成的格式**不得**写入条目；缺口写入 `limitations`。

## entities / relations 条目（与 spec.json 一致）

实体：
```json
{"id": "egfr", "label": "EGFR", "type": "receptor", "compartment": "membrane",
 "transmembrane": true, "species": "human", "aliases": ["ERBB1"],
 "source": {"locator": "materials/paper.md#p2", "quote": "逐字原文", "confidence": "high"}}
```
关系：
```json
{"id": "r1", "from": "egfr", "to": "ras", "type": "activation",
 "modifier": "P", "evidence": "direct",
 "source": {"locator": "materials/paper.md#p4", "quote": "逐字原文"}}
```

## sources / uncertainty / change_log 条目

- `sources` 聚合条目：`{"element": "egfr", "locator": "materials/paper.md#p2",
  "quote": "逐字原文", "note": ""}`
- `uncertainty` 条目：`{"element": "r1", "kind": "direction_ambiguous", "note": "..."}`
- `change_log` 条目：`{"at": "ISO8601", "phase": "bootstrap|extract|layout|export|finalize",
  "action": "...", "detail": "..."}`；首条必须为 bootstrap 骨架落盘。

## 状态语义

- `success`：机制完整、无阻断错误、关键图元可溯源、导出件真实存在。
- `partial`：已交付可用骨架/部分图稿，但存在未解决的缺口（必须在 `limitations` 说明）。
- `failed` / `insufficient_evidence` / `input_error`：无法完成任务、材料不足或输入损坏；
  必须写明原因，不得以示例内容冒充正式结果。

## 硬性要求

1. 源文件、导出件、`result.json` 三者的路径互相对应，且都真实写入 `/app/output/`。
2. 每个关键实体与边必须有 `source.locator` + 逐字 `quote`（缺则记 warning，最终前修复）。
3. `change_log` 只增不改；`status` / `limitations` 如实反映实际完成度。
4. 不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。
