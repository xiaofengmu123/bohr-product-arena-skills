# 输出契约与字段对齐规范（sci-analysis-contract）

本文件是 SKILL.md §3 的展开规范，供 Agent 填 result.json 时逐字段对照。目标是让
result.json 与 case.json 的 `output_contract` **逐字段对齐**：缺不漏、名不改、未知键不删。

## 1. result.json 顶层字段语义

| 字段 | 类型 | 语义与填写规则 |
|-|-|-|
| `case_id` | string/null | 从 case.json 的 `case_id`/`caseId`/`challenge_id`/`id` 原样回填；找不到给 `null` 并置 `status=input_error` |
| output_contract 字段 | 视契约而定 | 逐字段回填；缺值按类型给 `null`/`[]`/`{}`，不改名、不删未知键 |
| `status` | string | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `sources` | array | 关键结论/数值/图元的溯源，每项 `{locator, quote, role}` |
| `uncertainty` | array/string | 不确定点及依据 |
| `pending_human` | array | 需人工确认的事项（字符串或 `{item, reason}`） |
| `change_log` | array | append-only 动作日志，每项 `{ts, action, detail?, delta?}` |
| `errors` | array | 错误现场（`{code, detail, ...}`） |
| `degradations` | array | 降级说明 |
| `runtime` | object | `{started_at, completed_at, script_version, command}` |
| `_output_contract_locator` | string | 契约来源定位（内部元数据，如 `case.json#output_contract`） |
| `_output_contract_meta` | array | 逐字段的类型/默认值/来源（内部元数据） |

`_` 前缀为内部元数据，服务于"逐字段对齐"的可复核性；不影响官方契约字段。

## 2. status 选择规则

- `ok`：全部契约字段已回填有效值，产物齐全，与数据字典/金标准核对一致。
- `partial`：骨架或部分结果已落盘，仍有字段未完成或核对未通过（诚实标注 limits）。
- `insufficient_evidence`：材料不足以支撑结论，缺值写 `not_reported`/`pending_human`，列出缺口。
- `input_error`：输入缺失/坏 JSON/无法确定 case_id 等输入级错误。

## 3. sources 项与 change_log 项

sources 每项：`locator`（文件 + 行/段落定位，如 `materials/data.csv#row12` 或
`case.json#output_contract.statistics`）、`quote`（材料中的逐字原文）、`role`（该证据支撑哪个结论）。

change_log 每项：`ts`（ISO 时间戳）、`action`（动作名）、`detail`（上下文）、`delta`（行数/字段数变化）。
规则：**只能追加，不重写历史**；每次落盘、每次修正、每次失败恢复都要留痕。

## 4. contract_map.json（字段对齐账本）

`init` 产出 `/app/work/contract_map.json`：

```json
{
  "generated_at": "...",
  "locator": "case.json#output_contract",
  "field_count": 6,
  "fields": [
    {"field": "statistics", "type": "object", "default": {}, "source": "contract_key"}
  ],
  "warnings": []
}
```

- 契约来源优先级：`output_contract` > `outputContract` > `output-contract` > `contract` > task.md 说明。
- 类型推断：`type` 声明优先；无声明时按字段名启发式（含 `path/url/file/output/src/table` 视为 string；
  以复数 `s` 结尾且含 `list/items/results/records/sources/tables` 视为 array）；其余一律 `null`（unknown）。
- 未知键与未知类型不丢弃，保留为 `null` 并在 meta 中标注 `type=unknown`，供人工复核。

## 5. 路径字段约定

- 主产物路径字段（如 `cleaning_table_path`/`figure_path`/`statistics` 中引用的文件）优先写
  `/app/output/` 下的**绝对路径**；只写相对路径时以 `/app/output/` 为基准。
- 每个可编辑主产物都要有可复核的预览：图 = SVG + PNG；文档 = docx/pptx/xlsx + PDF/PNG。
- 缺失产物不写假路径，写 `not_reported` 或 `pending_human`，并在 `uncertainty` 说明。

## 6. 字段对齐核对（validate 自动执行）

`python3 -B scripts/main.py validate ...` 会重新从 case.json 推导契约并检查：
1. result.json 存在且是合法 JSON 对象；
2. `case_id` 非空、`status` 在允许集合内；
3. `sources` 每项含 locator/path 与 quote；
4. `change_log`/`uncertainty`/`pending_human` 存在；
5. 契约字段**一个不少**（列出 missing）；
6. 路径型字段指向的文件实际存在（列出缺失产物）。

任一检查失败即 `passed=false`，Agent 应据此修正而非掩盖。
