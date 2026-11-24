# result.json 输出契约（证据溯源强化版）

本文件定义 `/app/output/result.json` 的完整结构，供 Agent 与复核脚本共用。
字段名与题面 `output_contract` 要求保持一致；未知键原样保留、不改名不删除。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，来自 `case.json` 的 `case_id` / `id` / `armiId` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `summary` | object | 总体判定与计数，见 §2 |
| `review_report` | object | 条目化问题清单，见 §3 |
| `evidence` | object | 证据溯源结构，见 §4（本版核心） |
| `sources` | array | 实际核验过的来源；未取得写 `not_verified` |
| `uncertainty` | array | 无法确认、需复核的条目 |
| `pending_human` | array | 需人工裁决的条目 |
| `limitations` | array | 已知限制与缺口 |
| `errors` | array | 脚本/输入错误（含 finding 校验失败） |
| `degradations` | array | 降级处置记录 |
| `change_log` | array | 增量修改记录（阶段、动作、条目） |
| `runtime` | object | 预算与阶段描述（确定性，不写墙钟时间） |

`case.json` 若含 `output_contract`，其全部键**原样回填**到顶层：缺值给
`null`/`[]`/`""`，未知键保留。标准字段与 contract 冲突时标准字段优先。

## 2. summary

```json
{
  "verdict": "pass | issues_found | needs_review | unchecked | insufficient",
  "counts": {
    "total": 0, "critical": 0, "major": 0, "minor": 0,
    "suggestion": 0, "by_category": {}
  },
  "coverage": {"categories": [], "sections": [], "unreviewed": []}
}
```

`verdict` 必须诚实：材料不足为 `insufficient`，未开始为 `unchecked`。

## 3. finding（逐条问题）

每条 finding 必填键（缺失会被 `finalize` 标 `needs_review` 并记入 `errors`）：

| 键 | 说明 |
|---|---|
| `id` | 稳定 id，如 `F0001`（零填充，字典序可排序） |
| `category` | canonical key：`citation_support / fact_source / evidence_location / numerical_statistical / argumentation / academic_norm / method_design` |
| `finding_type` | `factual_error / inconsistency / unsupported_claim / overreach / source_unreliable / missing_control / norm_violation / evidence_insufficient / suggestion` |
| `severity` | `critical / major / minor / suggestion`，必须与问题性质匹配 |
| `verdict` | `fully_supported / partially_supported / unsupported / undeterminable`（引用支持档）或 `pass / needs_review / insufficient`（非引用项） |
| `locator` | 结构化定位，见 §5 |
| `quote` | **逐字**摘录原文，不得改词或增删关键数字 |
| `claim` | 对被审断言的**转述**，须标注为转述 |
| `basis` | 判定依据（规则 id + 理由），如 `rule C2; 引文仅覆盖亚组` |
| `source_ref` | 被引来源定位（DOI/URL/参考文献号/数据集条目）；未取得写 `not_verified` |
| `suggestion` | 修改建议 |
| `confidence` | `high / medium / low` |
| `review_status` | `verified / needs_review / unchecked` |

可选键：`quote_span`（字符区间）、`source_status`（`verified / not_verified /
not_reported`）、`evidence`（追加证据数组，用于反面证据/上下文）。

## 4. evidence（证据溯源，本版核心差异）

```json
{
  "evidence_map": [
    {
      "evidence_id": "sha256 前 16 位，确定性生成",
      "finding_id": "F0001",
      "role": "primary_support | counter_evidence | context",
      "locator": {"page": 3, "para": 2, "section": "Results",
                  "table": null, "figure": null, "line": null,
                  "raw": "p.3, para 2, Results"},
      "quote": "逐字摘录",
      "quote_span": {"start": 0, "end": 120},
      "quote_hash": "sha256(规范化 quote) 前 16 位",
      "basis": "rule C2; …",
      "source_ref": "not_verified | DOI/URL/ref",
      "source_status": "verified | not_verified | not_reported",
      "review_status": "verified | needs_review | unchecked"
    }
  ],
  "conflicts": [
    {
      "conflict_id": "C0001",
      "type": "source_conflict | inconsistency",
      "left_evidence_id": "…", "right_evidence_id": "…",
      "note": "两处证据均保留，不隐藏冲突"
    }
  ],
  "provenance": {"sources": []},
  "integrity": {
    "quote_hash_algorithm": "sha256",
    "normalization": "strip_and_collapse_whitespace",
    "verified": false, "mismatches": []
  }
}
```

要点：

- 每条 finding 至少生成一条 `primary_support` 证据；`evidence` 数组里的条目按
  `role=counter_evidence` 或 `context` 追加，用于记录反面证据与上下文。
- `quote_hash` 由脚本 `finalize` 确定性生成；`integrity` 子命令会重新计算并
  核对，同时把规范化 quote 回放到抽取文本中验证逐字存在。
- 同一指标两处不一致时，两条证据都要进入 evidence_map，并在 `conflicts` 中
  相互引用，不得只保留一方。

## 5. 结构化 locator

`locator` 优先写成对象（可机器解析），`raw` 保留可读定位串：

```json
{
  "page": 3, "para": 2, "section": "Results",
  "table": null, "figure": null, "line": null,
  "raw": "p.3, para 2, Results"
}
```

- PDF：以抽取文本的 `----- PAGE n / N -----` 定位到页与段；
- 文本/表格材料：`{"file": "materials/paper.md", "line": 42,
  "raw": "materials/paper.md#L42"}`；
- 图表/数据集：`{"figure": "Fig 2b", "raw": "Fig 2b"}` 或
  `{"table": "Table 1", "row": 3, "raw": "Table 1, row 3"}`。

## 6. change_log 条目

```json
{"seq": 1, "stage": "phase0|phase1|phase2|phase3",
 "action": "scaffold|add_finding|revise_finding|finalize|degrade",
 "target": "F0001 或 result.json", "note": "…"}
```

`seq` 自增，保证多次运行内容可复现。
