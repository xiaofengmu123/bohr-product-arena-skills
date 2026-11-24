# result.json 输出契约与字段语义

本文件定义 `/app/output/result.json` 的完整结构。`scripts/finalize.py` 自动生成
骨架、合并 finding、校验 quote 并统计；Agent 只需按本契约逐条落盘，字段名与
枚举值必须与下文一致，评分方可机器复核。

## 1. 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 案例标识，取自 `/input/case.json`；缺失写 `unknown` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，必须诚实 |
| `summary` | object | 总判与计数（见 §2） |
| `review_report` | object | findings 清单 + 覆盖矩阵（见 §3） |
| `evidence` | object | evidence_map（见 §4） |
| `sources` | array | 实际核验过的来源，未取得写 `not_verified`，无虚构 DOI/URL |
| `uncertainty` | array | 显式不确定性条目（见 §5） |
| `pending_human` | array | 待人工复核的 finding id 或描述 |
| `limitations` | array | 材料/工具/权限缺口，逐条写明 scope + detail |
| `errors` | array | 解析/读写/缺文件错误 |
| `degradations` | array | 降级处置记录（缺 pdftotext、扫描版、坏编码等） |
| `change_log` | array | 事件记录（scaffold/finalize/人工修订），含 time/event/detail |
| `runtime` | object | 预算与运行信息（见 §6） |
| `output_contract.*` | mixed | 若 `case.json` 含 `output_contract`，其全部字段原样回填 |

`output_contract` 回填规则：保留原键名、不改名、不删未知键；缺值给
`null`/`[]`/`""`。同时原样存入 `output_contract` 键；标准字段优先，不覆盖。

## 2. summary

```json
{
  "verdict": "pass|issues_found|needs_review|unchecked|insufficient",
  "counts": {
    "total": 0,
    "critical": 0, "major": 0, "minor": 0, "suggestion": 0,
    "quote_verified": 0, "quote_unverified": 0,
    "by_category": { "...": 0 }
  }
}
```

`verdict` 与 status 独立：verdict 描述结论，status 描述运行完成度。推导规则：
有 critical/major/minor → `issues_found`；仅 suggestion → `pass`；有
`needs_review` → `needs_review`；无 finding → `unchecked`；材料不足 →
`insufficient`。`meta.json` 的 `overall_verdict`/`overall_status` 可覆盖。

## 3. review_report.finding（单条，7 必填）

```json
{
  "id": "F001",
  "category": "citation_support",
  "finding_type": "overreach",
  "severity": "major",
  "verdict": "partially_supported",
  "locator": "p.3, para 2",
  "quote": "逐字摘录",
  "claim": "对被审查断言的转述",
  "basis": "判定依据（为何这样判）",
  "source_ref": "DOI/URL/参考文献编号 或 not_verified",
  "suggestion": "修改建议",
  "confidence": "high|medium|low",
  "review_status": "confirmed|needs_review",
  "quote_verified": true,
  "quote_match_locator": "texts/paper.txt p.3",
  "evidence_strength": "direct|indirect|absent|undeterminable"
}
```

必填七项：`id / category / finding_type / severity / verdict / locator / quote`
+ `suggestion`。`quote_verified` 由 finalize 脚本按"quote 是否在抽取文本中逐字
匹配"自动重算，不得手工标 true 掩盖不可追溯结论。

`review_report.coverage_matrix` 记录"类别 × 章节"覆盖状态，状态枚举：
`reviewed|partial|not_reviewed|not_applicable|not_available|needs_review`。
未覆盖的类别/章节必须显式标出，不得默认视为已查。

## 4. evidence.evidence_map

每条 finding 一项，含 `finding_id/locator/quote/basis/source_ref/quote_verified`。
用于机器复核"位置 + 摘录 + 依据"是否齐全。

## 5. uncertainty 显式化（本版重点）

不确定性与限制不写进正文一句话，而是结构化落盘。条目：

```json
{
  "id": "U001",
  "type": "missing_source|version_conflict|ambiguous_reference|ocr_failure|insufficient_evidence|source_unverified|quote_not_traceable",
  "description": "具体缺口",
  "affects": ["F001", "category:citation_support"],
  "resolution": "pending_human|not_required"
}
```

`type` 语义：

- `missing_source`：被引来源无法取得，无法判定支持程度
- `version_conflict`：预印本与正式版不一致，无法确定依据版本
- `ambiguous_reference`：引文指向歧义，无法确定对应文献
- `ocr_failure`：扫描版/OCR 失败，文本不可靠
- `insufficient_evidence`：材料本身不足以判断
- `source_unverified`：来源存在但未核实出版状态/撤稿
- `quote_not_traceable`：finalize 脚本无法在原文中逐字匹配 quote

`affects` 显式指向受影响的 finding 或类别；`limitations` 记录工具/权限/时间
缺口，两者都写，不互相替代。

## 6. runtime

```json
{
  "started_at": "ISO8601 UTC",
  "finished_at": "ISO8601 UTC 或 null",
  "wall_seconds": null,
  "budget_seconds": 900,
  "budget_requests": 64,
  "model_requests_used": null
}
```

`model_requests_used` 由 Agent 依据实际消耗如实填写；不知道就写 `null`，禁止
编造评测分数或运行结果。

## 7. change_log

每项 `{time, event, detail}`。event 至少含 `scaffold`（落骨架）、`finalize`
（合并刷新）、`revision`（人工修订）。中断恢复后追加记录，不覆盖旧记录。

## 8. 降级与不编造

- 空输入目录 → `status=insufficient_evidence`，verdict=`insufficient`，
  limitations 列出缺口。
- 坏 JSON/坏编码 → 记入 `errors`/`degradations`，跳过该文件继续其余材料。
- 缺 pdftotext / 扫描版 PDF → 该 PDF 标 `needs_ocr`，相关结论标
  `undeterminable`/`needs_review`，不硬判。
- 只取得摘要/原文取不到 → `undeterminable` + `pending_human`，source_ref 写
  `not_verified`。
- 引文严重歧义/来源版本不一致 → 两条证据都记录，标 `source_conflict`，进
  `uncertainty`。
- 写盘失败 → 降级到最小合法产物；仍失败则 stderr 报错并退出非 0。
