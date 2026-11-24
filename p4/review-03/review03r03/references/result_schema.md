# result.json 契约（供脚本与复核人对照）

`/app/output/result.json` 是机器可复核的主产物；`/app/output/report.md` 是
人类可读、可编辑的同源渲染。二者均由 `scripts/main.py finalize` 确定性生成。

## 1. 顶层字段

| 字段 | 类型 | 取值 / 含义 |
|---|---|---|
| `case_id` | string | 保留输入标识，来自 case.json 的 `case_id/caseId/id` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `result_status` | string | `pass \| issues_found \| needs_review \| not_checked`（通过/问题/待复核/未检查） |
| `summary` | object | `verdict` + `counts` + `by_facet` + `by_type` |
| `review_report` | object | `findings[]` + `checked_facets[]` + `unchecked[]` |
| `evidence` | object | `evidence_map`：finding id → 定位 + 逐字 quote + 依据 + 复核状态 |
| `sources` | array | 实际核验的材料文件清单（path/kind/chars/pages/status） |
| `uncertainty` | array | 无法确认、需外查的条目 |
| `pending_human` | array | 需作者/人工决策的条目 |
| `limitations` | array | 未完成项、材料缺口、工具限制 |
| `errors` | array | 机器或流程错误 |
| `degradations` | array | 降级处置记录（缺 pdftotext、扫描件、坏编码等） |
| `numeric_checks` | array | 脚本检出的数值矛盾候选，`needs_review=true`，非结论 |
| `change_log` | array | 确定性变更记录（seq/actor/action/version/digest） |
| `runtime` | object | schema/script 版本、input_digest、python 版本（无墙钟时间） |

除上表外，若 `/input/case.json` 含 `output_contract`，其全部字段由
`scaffold`/`finalize` **原样回填**到顶层：不改名、不删未知键、缺值保留为
`null`/`[]`/`""`。

## 2. finding 对象（六必填 + 语义字段）

```json
{
  "id": "F001",
  "facet": "results_reporting",
  "check_class": "statistical_reporting",
  "finding_type": "factual_error",
  "severity": "major",
  "locator": "p.3 ¶2",
  "quote": "逐字摘录的原文",
  "basis": "判断依据",
  "suggestion": "修改建议",
  "confidence": "high | medium | low",
  "review_status": "confirmed | needs_review | source_conflict"
}
```

- 六必填：`id/facet/locator/quote/severity/suggestion`。
- 枚举见 `references/checklist.md`：facet 5 值、check_class 6 值、
  finding_type 5 值、severity 5 值。
- `quote` 必须逐字；脚本核验失败时 `review_status` 自动降为 `needs_review`
  并记入 `degradations`。
- `basis` 是「为什么这么判」，不写空话；无法核验来源时写 `not_verified`。

## 3. evidence_map 结构

```json
{
  "F001": {
    "locator": "p.3 ¶2",
    "quote": "逐字摘录",
    "basis": "判断依据",
    "finding_type": "factual_error",
    "severity": "major",
    "review_status": "confirmed"
  }
}
```

复核人只凭 `locator + quote` 即应能独立定位原文并复现判定。

## 4. output_contract 回填规则

- 深拷贝 `output_contract` 到 result.json 顶层，逐键原样保留；
- 核心字段（case_id/status/...）用 `setdefault` 补齐，不覆盖 contract 已定义
  的同名键；
- 未知键不删除、不重命名。

## 5. status 诚实性规则

| 情况 | status | 说明 |
|---|---|---|
| 缺 case.json 且无材料 | `input_error` | errors 记录 |
| 有 case.json 但无材料 | `insufficient_evidence` | limitations 列出缺口 |
| 有材料但未走查全部 facet | `partial` | unchecked 列出未查面 |
| 全部 facet 已走查且 patch 标 complete | `ok` | result_status 由发现推导 |
| 任一 finding 为 pending_review | `needs_review`（result_status） | 进 uncertainty/pending_human |

绝不编造数据、数值、引用、DOI、URL 或评测分数；缺失写 `not_reported`/
`not_verified`。

## 6. 非 finding 内容的写入方式

Agent 审查完成后，把 `verdict/result_status/checked_facets/sources/
uncertainty/pending_human/limitations/errors/degradations/complete` 写入
`/app/work/result_patch.json`（一个顶层 dict），`finalize` 按确定性规则合并：

```json
{
  "verdict": "存在需修正的统计方法问题",
  "result_status": "issues_found",
  "complete": true,
  "checked_facets": ["research_design", "variable_definition",
                     "statistical_methods", "sample_size_power", "results_reporting"],
  "uncertainty": ["...无法确认的条目..."],
  "pending_human": ["...需作者确认的条目..."],
  "limitations": ["...未完成或无法判断的项..."]
}
```

`findings` 不写进 patch，而是逐条写成 `/app/work/findings/<id>.json`（一个文件
一个对象），由 `finalize` 读取、校验、排序、重编号。

## 7. report.md 结构（同源渲染）

审查报告 = 摘要（case_id/status/result_status/verdict）→ 计数汇总 → 逐条发现
（含逐字 quote 代码块）→ 数值一致性候选 → 证据映射 → 来源 → 不确定性/待复核/
局限/错误/降级 → 变更记录。可编辑，且与 result.json 一一对应。
