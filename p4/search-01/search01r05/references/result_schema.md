# result.json 规范与契约回填规则

本文件说明 `result.json` 的结构、候选记录字段，以及 output_contract 的原样回填规则。与本说明保持一致的是 `scripts/core.py` 的实现；两者冲突时以脚本实现为准，并请反馈修正说明。

## 1. 三层发布结构

`result.json` 顶层同时包含三部分，缺一不可：

1. **规范字段**（canonical，SKILL.md 第 3 节列出的固定键）。
2. **契约原名字段**（contract keys）：题目 `output_contract` 声明的每个字段按原名、原序写进顶层；值优先从规范字段对齐。
3. **契约回填报告**（`contract_report`）：逐字段审计。

### 契约辨认

`output_contract` 可能来自 `case.json` 的多个键名，或 `task.md` 中的 Markdown 表。`scripts/core.py` 的 `parse_case` 按以下顺序发现：

1. `case.json` 顶层键：`output_contract` / `outputContract` / `output` / `result_schema` / `schema` / `required_output` / `required_fields` / `expected_fields` / `contract` / `fields` / `output_schema`。
2. `task.md` 中的围栏 JSON 块或 `| 字段 | 要求 |` Markdown 表。

### 契约归一化

任意形式统一成有序字段表 `[{name, required, type, description}]`：

- 字符串：提取反引号字段名 `` `results` ``，或整体作为单字段名；可 JSON 解析时递归。
- 列表：元素为字符串或 `{name/field/key, required, type, description}`。
- 字典：`fields`/`properties` 子项优先（JSON-schema 的 `required` 列表额外补入，保证不丢字段）；否则每个键视为一个字段，值为字符串视作描述、为字典取 `required/type/description`。

### 逐一对齐（回填）

对每个契约字段执行 `resolve_contract_value`：

1. `canon_key(name)` 命中别名桥表（`case_id/status/results/evidence/sources/uncertainty/pending_human/errors/degradations/change_log/limitations/status_notes/runtime/query_plan` 及大小写/连字符变体）→ 填规范值，`source=canonical:<key>`。
2. 顶层存在原名键 → `source=raw:<name>`。
3. 规范键存在 → `source=normalized:<key>`。
4. 都不存在 → 回填：`type` 含 `array/list` 时给 `[]`（`empty_backfilled`），否则 `null`（`null_backfilled`）。

任何情况下**不改名、不删未知键**；回填后字段键保证存在。

### contract_report

```json
{
  "spec_version": "1.1.0",
  "total_fields": 6,
  "filled": 5,
  "null_backfilled": 1,
  "empty_backfilled": 0,
  "fields": [
    {"name": "case_id", "required": true, "type": null,
     "source": "canonical:case_id", "value_preview": "case:case",
     "filled": true, "status": "filled"}
  ]
}
```

## 2. 规范字段说明

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取自输入；缺失时兜底 `case:<文件名>` |
| `status` | string | `ok`（有结果且无未决问题）/ `partial`（有结果但有缺失、冲突或降级）/ `insufficient_evidence`（无候选）/ `input_error`（输入不可解析） |
| `results` | list | 去重、打分、排序后的候选 |
| `evidence` | list | 每次查询/来源的溯源记录 |
| `sources` | list | 去重后的数据库/来源名集合 |
| `uncertainty` | dict | `missing_metadata / conflicts / coverage_gaps / notes` |
| `pending_human` | list | 需人工判定项（如 relevance 未标注） |
| `errors` / `degradations` | list | 解析/接口/降级问题 |
| `change_log` | list | `time/action/reason/status`，全程留痕 |
| `query_plan` | dict | 规划结果与关键词核验清单 |
| `runtime` | dict | `start_unix/end_unix/seconds` |
| `result_schema_version` | string | 本规范版本 |

## 3. 候选记录字段（results[]）

| 字段 | 必需 | 说明 |
|-|-|-|
| `rank` | 由脚本生成 | 排序位次，从 1 起 |
| `title` | **是** | 无标题的记录被跳过（防编造） |
| `authors` | 否 | 字符串或数组；导入时按 `;,、` 拆分 |
| `year` | 否 | 尽力转 int |
| `journal` | 否 | 期刊/会议名 |
| `doi` | 否 | 经过 `https://doi.org/` 前缀剥离与大小写归一 |
| `url` | 否 | 除 DOI 外的可打开链接 |
| `database` | 否 | 来源库，参与来源可靠度 |
| `relevance` | 否 | 0–1；缺失但有 `relevance_reason` 记 0.5，两者全缺记 0.0 |
| `relevance_reason` | 否 | 一段话，须对应 `evidence[]` 的 locator+quote |
| `score` | 由脚本生成 | 见 query_and_dedup.md 的打分公式 |
| `evidence` | 否(强烈建议) | `[{"locator","quote","source"}]`，逐字引用 |

元数据缺失与冲突：缺失字段列入 `uncertainty.missing_metadata`（DOI 存在时 url 可派生，不重复计缺）；同一指标多来源不一致时以 `source_conflict` 全部记录，不擅自取舍。