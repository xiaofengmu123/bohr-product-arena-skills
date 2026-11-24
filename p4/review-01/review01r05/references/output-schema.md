# 输出契约与 result.json 完整规格

本文件定义 `/app/output/result.json` 的字段、来源与判据。核心原则：
**output_contract 的每个字段必须原样、逐字段回填；不改名、不删除、未知键
写 null/[]/占位值**，并由 `contract_audit` 给出逐字段可复核的对齐结论。

## 1. 顶层结构

`result.json` 顶层 = 三部分并集：

1. **case_id**：原样保留 `case.json` 的 `case_id`（或 `id`/`task_id`）。
2. **output_contract 全部顶层字段**：名称与层级与 `case.json` 的
   `output_contract` 完全一致；dict 逐键、list 逐项回填。
3. **信封字段**（见第 3 节）。

若 output_contract 的某个键与信封保留键同名（如 `status`、`result`、
`sources`），**契约值占据顶层**，信封值整体移入 `_skill_envelope.<key>`，
并在 `contract_audit.collisions` 中记录；契约字段永不因信封而丢失或改名。

## 2. output_contract 处理规则

- `output_contract` 为 dict：逐键回填；嵌套 dict/list 保留原层级。
- `output_contract` 为 list：
  - 元素为字符串 → 视为字段名，值填 `null`；
  - 元素为 dict → 并入；
  - 其他元素 → 用索引名兜底（`"0"`、`"1"`…）。
- 契约缺失（`case.json` 无 `output_contract`）：`contract_audit` 记为
  空契约，不回填任何契约字段。
- 未知键一律写占位（`null` / `[]`），不猜测类型与内容。

`scripts/main.py init` 会生成 `/app/work/draft.json`，其 `contract_values`
即为契约骨架（`skeleton_of` 递归生成），Agent 在 `draft.json` 内填入真实
值，`check` 校验键名一一对应（无 missing、无 extra），`build` 落盘。

## 3. 信封字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `status` | str | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | list | 材料清单：`source_id`、路径、类型、段落数、解析问题 |
| `uncertainty` | list | 无法确定或需保留的歧义/冲突项 |
| `pending_human` | list | 需人工复核/补料项 |
| `errors` | list | 输入解析错误（坏 JSON、缺文件等） |
| `degradations` | list | 降级事实（OCR 缺失、PDF 兜底解析、超长跳过等） |
| `changes` | list | 本轮相对骨架的实质改动摘要 |
| `change_log` | list | append-only 变更记录（见第 5 节） |
| `runtime` | obj | 耗时、预算（900s/64 请求/16384 词元）、版本 |
| `contract_audit` | obj | 逐字段对齐审计 + 冲突 + 契约规范副本 |
| `result` | obj | 审查主结构（见第 4 节） |

`status` 取值判据：

- `ok`：与材料相关的六分面均已检查且各有 finding 或已明确列入
  `unchecked_items`，契约字段已填。
- `partial`：完成部分检查，缺口写入 `unchecked_items`/`pending_human`。
- `insufficient_evidence`：材料缺失/不可读，无法开展审查。
- `input_error`：`case.json` 坏 JSON 等输入错误。

## 4. 审查主结构 `result`

```json
{
  "findings": [],
  "unchecked_items": [],
  "verdict_summary": {}
}
```

### 4.1 finding 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `finding_id` | str | 全局唯一，如 `F01` |
| `facet` | str | 六分面之一（见 review-checklist.md） |
| `severity` | str | `critical/major/minor/suggestion` |
| `issue_type` | str | `factual_error/insufficient_evidence/standards_issue/suggestion/source_conflict` |
| `verdict` | str | `confirmed/to_review/not_checked` |
| `claim` | str | 本文论断或问题描述（一句话） |
| `location` | str | 原文定位，如 `D1::para7` 或 `D1::p3#para2` |
| `quote` | str | 逐字原文（可跨行不跨段）；`check` 必须逐字命中 |
| `rationale` | str | 判定依据 |
| `revision_suggestion` | str | 可执行修改建议 |
| `confidence` | str | `high/medium/low` |
| `contract_ref` | str | 可选：该 finding 支撑的 output_contract 字段路径 |
| `evidence` | list | 可选：`[{source_id, locator, quote}]` 逐字证据 |

quote 硬约束：**必须能从材料段落中逐字命中**（空白折叠后比较）。`check`
对每条 finding 校验；未命中则回原文重抄，直到无 FAIL。不确定项写
`verdict=to_review`，不得凭空 `confirmed`。

### 4.2 unchecked_items

六分面中未完成检查的部分逐条列出：`facet`、`status`（`not_checked`）、
`reason`、`evidence_limits`。不适用也须说明原因。

### 4.3 verdict_summary

- `overall_verdict`：`pass/issues_found/to_review/not_checked/inconclusive`
- `counts`：findings 总数、按 facet/severity/verdict 计数
- `coverage`：每分面 `checked` 与 findings 数
- `contract`：契约字段 total/satisfied/defaulted/absent

## 5. change_log（append-only）

`/app/work/change_log.jsonl` 每行一条：`{seq, ts, action, summary, files}`。
`build` 读取全部历史写入 `result.change_log`；同一输入重复运行时
findings/counts/coverage 结构一致，只有 `ts`/`runtime.wall_seconds` 等易变量
变化（确定性要求）。

## 6. 最小合法性

任何时刻（init 骨架、异常兜底）`result.json` 都必须是合法 JSON，至少含
`case_id`、`status`、`result.findings=[]`、契约字段占位、`contract_audit`。
坏 JSON/空目录/缺材料均退出码 0，不抛异常。
