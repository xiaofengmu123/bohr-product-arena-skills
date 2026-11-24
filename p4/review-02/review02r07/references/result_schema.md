# result.json 契约与字段语义

本文件定义 `/app/output/result.json` 的完整契约。脚本 `scaffold.py` 落盘骨架、
`finalize.py` 合并 findings 后刷新；两条路径都遵循本契约。人工复核以本文件为准。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `case_id` | string | 是 | 案例标识，来自 `/input/case.json` 的 `case_id`/`caseId`/`id`；缺失时取文件名或 `unknown_case` |
| `status` | enum | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `summary` | object | 是 | 总览：verdict、counts、coverage |
| `review_report` | object | 是 | `findings` 与 `summary_notes` |
| `evidence` | object | 是 | `evidence_map`，每条 finding 的证据副本 |
| `sources` | array | 是 | 实际核验过的来源；未取得写 `not_verified` |
| `uncertainty` | array | 是 | 不确定项；每条可含 `item_id/locator/reason` |
| `pending_human` | array | 是 | 待人工复核项 |
| `limitations` | array | 是 | 方法/材料/权限导致的已知限制 |
| `errors` | array | 是 | 解析失败、跳过文件、结构错误 |
| `degradations` | array | 是 | 降级处置（needs_ocr/needs_pdftotext/超限跳过等） |
| `change_log` | array | 是 | 步骤事件（scaffold/finalize），增量追加、幂等 |
| `runtime` | object | 是 | 技能版本、已完成阶段、模型请求数 |

若 `/input/case.json` 含 `output_contract`（或 `outputContract`/`contract`），其全部
字段**原样回填到顶层**：不改名、不删未知键；缺值给 `null`/`[]`/`""`。标准字段与
合同字段同名时，标准字段优先，且 `case_id` 始终置顶覆盖。

## 2. summary

```json
{
  "verdict": "issues_found",
  "counts": {
    "total": 3, "critical": 0, "major": 2, "minor": 1, "suggestion": 0,
    "by_category": {"citation_support": 1, "numerical_statistical": 2},
    "by_severity": {"critical": 0, "major": 2, "minor": 1, "suggestion": 0}
  },
  "coverage": {
    "categories_reviewed": ["citation_support"],
    "sections_reviewed": ["methods"],
    "not_covered": ["fact_source"]
  }
}
```

- `verdict`：`pass` / `issues_found` / `needs_review` / `unchecked` / `insufficient`。
- `counts.by_category` 与 `by_severity` 缺省给 0。
- `coverage.not_covered` 由 finalize 自动按七类检查项与 findings 的差集计算；Agent
  可在 `meta.json` 中补 `sections_reviewed`。

## 3. review_report.findings 条目

每条 finding 是一个对象：

```json
{
  "id": "F001",
  "category": "numerical_statistical",
  "finding_type": "inconsistency",
  "severity": "major",
  "verdict": "unsupported",
  "locator": "p.4, para 2",
  "quote": "N=120 subjects were enrolled",
  "claim": "转述：方法部分声称 N=120",
  "basis": "结果表 1 报告 N=118，且未说明 2 例排除原因",
  "source_ref": "not_verified",
  "suggestion": "统一样本量并补充排除标准",
  "confidence": "high",
  "review_status": "verified"
}
```

必填键（缺一即被 finalize 丢弃并记入 `degradations`）：

`id`、`category`、`finding_type`、`severity`、`verdict`、`locator`、`quote`、
`suggestion`。

可选键及默认值：

| 键 | 默认 | 语义 |
|---|---|---|
| `claim` | `""` | 对被审查断言的转述 |
| `basis` | `""` | 判定依据（引文语义对照/两条证据等） |
| `source_ref` | `"not_verified"` | 被引用来源定位；未取得不得填 DOI/URL |
| `confidence` | `"medium"` | high/medium/low |
| `review_status` | `"needs_review"` | `verified` / `needs_review` / `pending` |

`verdict` 的取值：

- `citation_support` 类必须用四档：`fully_supported` / `partially_supported` /
  `unsupported` / `undeterminable`。
- 其它类别可用：`confirmed` / `unsupported` / `inconsistent` /
  `undeterminable` / `not_applicable` / `needs_review`（非强制，但建议收敛到该集合）。

`severity` 只允许 `critical`/`major`/`minor`/`suggestion`；非法值被归一化为
`suggestion` 并记入 degradations。

## 4. evidence.evidence_map

每条与 finding 一一对应：

```json
{
  "finding_id": "F001",
  "locator": "p.4, para 2",
  "quote": "N=120 subjects were enrolled",
  "basis": "结果表 1 报告 N=118",
  "source_ref": "not_verified"
}
```

这是复核入口：任一结论都能经 `finding_id` 回到 finding，再经 `locator + quote`
回到原文。

## 5. sources / uncertainty / pending_human / change_log

- `sources`：只含实际核验过的来源。建议形如
  `{"ref_id": "Ref 12", "type": "doi|url|dataset|figure", "locator": "...", "verified": true}`。
  未取得来源在 finding 的 `source_ref` 写 `not_verified`，不写进 sources。
- `uncertainty` / `pending_human`：条目可用字符串或 `{item_id, locator, reason}`。
- `change_log`：`[{"step": "scaffold|finalize", "event": "...", "status": "...", "verdict": "...", "findings_count": 0}]`。
  相同签名不重复追加，保证重复 finalize 幂等。

## 6. runtime

```json
{
  "skill_version": "1.0.0",
  "phases_completed": ["discover", "scaffold", "finalize"],
  "model_requests_used": null
}
```

- `phases_completed` 由 `state.json` 推进，确定性推导。
- `model_requests_used` 由 Agent 按实际填写；未统计写 `null`，禁止编造。
- 墙钟时间与平台记录不在本文件内，避免破坏重复运行一致性。

## 7. 状态机与判定映射

| 情况 | status | verdict |
|---|---|---|
| 输入目录缺失/空 | `insufficient_evidence` | `insufficient` |
| case.json 坏且缺失 case_id | `input_error` | `unchecked` |
| 已审查且 meta.review_complete=true | `ok` | meta.overall_verdict 或按 findings |
| 已落盘 findings、含 critical/major | `partial`(未完成) 或 `ok` | `issues_found` |
| 已落盘 findings、仅 minor/suggestion 且待复核 | 同上 | `needs_review` |
| 无 findings 且未完成 | `partial` | `unchecked` |

`meta.json` 中 `overall_verdict` 由 Agent 在复核阶段写入，finalize 优先采用；
未写入时按 findings 自动推导，保持诚实、可复现。
