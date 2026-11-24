# 输出契约与契约引擎说明

本文说明 `result.json` 的字段来源、类型归一化规则与契约自检机制，是本 Skill“输出契约完整”这一设计取舍的实现依据。

## 1. 契约即模式（contract-as-schema）

`case.json` 中的 `output_contract`（亦识别 `outputContract`、`output_contract_schema`、`output_schema`、`result_contract`、`result_schema`、`contract`，在对象内递归查找，深度 ≤3）是 result.json 字段的**唯一事实来源**。脚本把它归一化成一棵“类型树”，并在三个阶段使用它：

1. `skeleton`：按契约生成 result.json，每个字段给缺省值（`string`→`null`、`number`→`null`、`boolean`→`null`、`array`→`[]`、`object`→递归缺省对象）。
2. `finalize`：从 `/app/work` 的协议/筛选/质量/证据/综合文件确定性组装，逐字段回填。
3. `selfcheck`：逐字段校验“是否存在 + 类型是否匹配”，并把未填字段列出，供增量补写。

若 `case.json` 不含 `output_contract`，回退到本 Skill 的内置领域契约（`references/review-protocol.md` 的五个环节字段）。

## 2. 类型归一化

| 契约写法 | 归一化类型 | 缺省值 |
|---|---|---|
| `"string"` / `"str"` / `"text"` | `string` | `null` |
| `"number"` / `"int"` / `"float"` | `number` | `null` |
| `"boolean"` / `"bool"` | `boolean` | `null` |
| `"array"` / `"list"` / 列表字面量 | `array` | `[]` |
| `"object"` / `"dict"` / 含 `fields` 或 `properties` 的对象 | `object` | 递归缺省对象 |
| 其他 | `any` | `null` |

嵌套对象示例：

```json
{"type": "object", "fields": {"query_blocks": "array", "total_hits": "number"}}
```

字段在骨架中写成：

```json
{"query_blocks": [], "total_hits": null}
```

`null` 与空容器视为“存在但未填”，由 `selfcheck` 报告为 `unfilled`；**缺失**才记 `missing`，两者都会阻止 `passed=true`。

## 3. result.json 的固定结构

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "…契约全部字段（含未知键，原样回填）…",
  "artifacts": [],
  "sources": [],
  "uncertainty": {},
  "change_log": [],
  "limitations": [],
  "errors": [],
  "degradations": [],
  "pending_human": [],
  "runtime_seconds": null
}
```

`case_id` 取自 `case.json` 的 `case_id`/`caseId`/`id`/`armiId`/`challenge_id`，缺失填 `"unknown"`。

## 4. `status` 语义

| 值 | 含义 | 触发 |
|---|---|---|
| `ok` | 五环节完成且结论可回链 | 契约字段齐全、筛选/证据/结论非空，且 selfcheck 无契约问题 |
| `partial` | 部分完成 | 骨架已落盘但上述条件不满足，须在 `limitations` 列缺口 |
| `insufficient_evidence` | 材料不足，不编造 | 空输入、无全文、检索为空且无法综合 |
| `input_error` | 输入不可解析 | case.json 损坏/坏编码 |

`skeleton` 先按“空输入→insufficient_evidence；无 case.json→partial；case.json 坏→input_error”落盘；`finalize` 只把 `partial` 向 `ok` 方向升级，**绝不**把 `insufficient_evidence`/`input_error` 改回 `partial`。

## 5. `change_log` 契约

每条记录为一个对象：

```json
{"ts": "ISO8601", "action": "skeleton|merge|finalize", "detail": "…", "fields": ["…被改的顶层字段…"]}
```

- `skeleton`：首次落盘骨架；
- `merge`：增量合并 `/app/work/payload.json` 时记录，`fields` 为实际发生变化（`sort_keys` 序列化后对比）的顶层键；
- `finalize`：组装时记录，`fields` 列出重算字段。

时间戳与 `runtime_seconds` 是**唯一**有意可变的痕迹字段；内容字段用 `sort_keys=True` 固定键序，保证同一输入重复运行内容一致。

## 6. 配套产物

`finalize` 在 `/app/output/` 生成四个可复核文件：

- `review.md`：人类可读综述（由 result.json 确定性组装）；
- `screening_log.json`：`{records, counts}`，counts 为 PRISMA 重算值；
- `evidence_map.json`：`{records, index}`，index 按 `evidence_id` 建索引；
- `contract_report.json`：逐字段 `{field, spec, present, type_ok, filled}` 对齐报告。

`result.json` 的 `artifacts` 列出上述产物相对路径。
