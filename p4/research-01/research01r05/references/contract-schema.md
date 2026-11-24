# 输出契约对齐规则（contract-schema）

本 Skill 的核心不变量：**`result.json` 的字段集合 = 题面 `output_contract` 全部字段 ∪ 标准补充字段**。以下规则由 `scripts/resultlib.py` 落实，模型不手工改 JSON。

## 1. 契约定位

在 `case.json` 中按下列优先级查找契约键（可在顶层或嵌套对象中）：

`output_contract` > `output_schema` > `result_schema` > `output_spec` > `result_contract`

契约本身接受三种形态：

| 形态 | 示例 | 归一化结果 |
|---|---|---|
| 字段名 → 规格 | `{"evidence_matrix": {"type": "array"}}` | 字段名 + 类型 |
| 字段名 → 示例值 | `{"evidence_matrix": []}` | 由示例值推断类型 |
| 字段名列表 | `["sources", "uncertainty"]` | 每个字段类型 `any` |
| 规格对象列表 | `[{"name": "sources", "type": "array"}]` | 取 `name/field/key/id` 定位字段名 |

字段顺序按契约中出现的顺序保留，保证两次运行结果一致。

## 2. 缺失字段的默认值

| 契约声明类型 | 缺失时回填 |
|---|---|
| `array` / `list` | `[]` |
| `object` / `dict` / `map` | `{}` |
| `number` / `integer` / `float` / `boolean` | `null`（不编造数值） |
| 其他 / 未声明 | `null` |
| 规格含 `default` | 深拷贝该默认值 |

规则：宁可给 `null` 也不编造数字；未知键一律保留，不重命名、不删除。

## 3. 标准补充字段（始终存在，除非契约已含同名）

`case_id`、`status`、`research_product`、`evidence_matrix`、`uncertainty`、`sources`、`change_log`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime_seconds`。

其中 `case_id` 取自 `case.json` 的 `case_id/caseId/id/armiId`；`status` 取值限于 `ok | partial | insufficient_evidence | input_error`。

## 4. change_log 语义

每次结构变更追加一条，不覆盖历史：

| action | 触发点 |
|---|---|
| `skeleton` | 落盘初始骨架 / 重开已有 result.json |
| `merge` | 合并 payload.json |
| `finalize` | 写入 runtime、contract_audit、报告 |

每条含 `ts`（UTC ISO 时间）、`action`、`detail`；时间戳是运行事实，允许随运行变化，其余字段顺序与内容保持确定性。

## 5. contract_audit（finalize 写入）

```json
{
  "contract_key": "output_contract",
  "present_contract_fields": ["evidence_matrix", "..."],
  "missing_contract_fields": [],
  "extra_fields": [],
  "checked_at": "2026-...Z"
}
```

`missing_contract_fields` 必须为空才是合法交付；非空说明骨架或 merge 阶段漏填，应立即用 `main.py skeleton` 或 `main.py merge` 修复。

## 6. 证据行最小字段

`evidence_matrix` 每行至少含 `id`、`locator`、`quote`；`quote` 必须是材料中的逐字片段，`locator` 定位到段落级（如 `p3 §2.2` 或 `materials/a.md#L12`）。结论的 `evidence_ids` 必须命中证据行的 `id`，否则 `selfcheck` 报 validation issue。
