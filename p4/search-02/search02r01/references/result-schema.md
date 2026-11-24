# result.json 字段规范（result-schema）

本文件定义 `/app/output/result.json` 的完整结构。所有字段均为 UTF-8；`*` 表示强制字段。除下述字段外，题目 `output_contract` 声明的全部字段必须**原样回填**（缺值给 `null` 或 `[]`，不改名、不删未知键）。

## 顶层

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id`* | string\|null | 取自输入 case.json 的 `case_id`/`id`/`challenge_id`；缺失为 `"unknown"` 或 `null` |
| `status`* | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `status_notes` | string | 对 status 的说明（含降级/未完成部分） |
| `research_question` | string\|null | 从输入或 task.md 提取的研究问题 |
| `sub_questions`* | array | 见下 |
| `query_plan`* | object | 见下 |
| `term_expansion`* | object | 见下 |
| `stop_decision`* | object | 见下 |
| `results`* | array | 候选文献，见下 |
| `screening`* | object | 全量筛选账本，见下 |
| `evidence`* | array | 证据账本，见下 |
| `evidence_map`* | object | 证据溯源映射，见下 |
| `sources`* | object | 数据库与本地材料清单 |
| `uncertainty`* | object | 缺失/冲突/覆盖缺口 |
| `pending_human` | array | 需人工补充的具体缺口 |
| `change_log`* | array | 增量变更日志（time/action/reason/status） |
| `errors` / `degradations` / `limitations` | array | 异常、降级、局限 |
| `runtime` | object | 运行预算与状态 |

## sub_questions[]（六要素拆解）

```json
{
  "id": "SQ1",
  "question": "可检索表述",
  "facets": {
    "object": "研究对象",
    "mechanism": "机制",
    "method": "方法",
    "time_window": "时间范围",
    "comparison": "比较关系",
    "evidence_requirement": "证据要求"
  },
  "keywords": ["..."],
  "status": "covered | partial | uncovered",
  "result_ids": [0, 2]
}
```

`status=uncovered` 的子问题必须同时出现在 `uncertainty.coverage_gaps` 与 `stop_decision.uncovered_sub_questions`。

## query_plan

```json
{
  "order": [{"step": 1, "sub_question_ids": ["SQ1"], "type": "前置|并行|补充", "query": "...", "rationale": "..."}],
  "dependencies": [{"from": "SQ1", "to": "SQ2", "type": "前置"}],
  "parallel_groups": [{"group": "G1", "sub_question_ids": ["SQ3", "SQ4"], "note": "..."}]
}
```

## term_expansion

```json
{
  "concepts": [{"concept": "研究对象", "variants": [{"term": "同义/缩写/跨语言/上下位/领域变体", "type": "...", "lang": "en|zh"}]}],
  "ambiguities": [{"term": "...", "issue": "...", "resolution": "加限定词或拆查询"}],
  "max_variants_per_concept": 6
}
```

## results[]（候选文献）

```json
{
  "rank": 1,
  "title": "...", "authors": ["..."], "year": 2024, "journal": "...", "doi": "...", "url": "...",
  "database": "paper_search|web|materials/...",
  "sub_question_ids": ["SQ1"],
  "relevance": 0.0,
  "relevance_reason": "CLM-001：...（必须引用 evidence_id）",
  "screening": "included|excluded|uncertain",
  "screening_reason": "...",
  "evidence": [ /* 见下 evidence 条目结构 */ ],
  "score": 0.0,
  "score_breakdown": {
    "relevance": 0.0, "evidence_strength": 0.0,
    "metadata_completeness": 0.0, "source_reliability": 0.0,
    "score": 0.0, "formula": "0.45*relevance + 0.30*evidence_strength + 0.15*metadata_completeness + 0.10*source_reliability"
  }
}
```

打分由脚本确定性计算：`relevance` 由 Agent 依据证据给出（0–1）；`evidence_strength` = 该记录证据条目强度的均值（逐字 quote + locator 且本地命中 = 1.0；quote+locator 但外部来源 = 0.6；只有其一 = 0.3；均无 = 0.0）；`metadata_completeness` = title/authors/year/journal/doi 五字段命中率；`source_reliability` 按来源（本地材料 1.0 > paper_search/Crossref/OpenAlex 0.9 > arXiv/PubMed 0.8 > 出版商域名 0.7 > 一般 web 0.5）。

## evidence[] / results[].evidence[]（证据条目）

```json
{
  "evidence_id": "EV-001",
  "claim_id": "CLM-001",
  "claim_text": "被支撑的结论（逐句）",
  "kind": "relevance|bibliographic|screening|query|conflict|gap|metadata",
  "source_ref": "materials/paper.md#p3 或 paper_search 或 web:https://...",
  "locator": {"file": "materials/paper.md", "page": 3, "paragraph": 2, "span": [12, 340]},
  "quote": "逐字引文（不改写）",
  "quote_hash": "sha1:<规范化 quote 的 sha1>",
  "source": "来源名称",
  "timestamp": "ISO8601",
  "confidence": "high|medium|low",
  "local_verified": true|false|null
}
```

`local_verified`：`true` 表示本地文本类材料规范化（空白折叠）后命中逐字 quote；`false` 表示指向本地材料但未命中；`null` 表示外部来源（脚本无法二次核验，trusted-as-recorded）。

## evidence_map（证据溯源强化，本版核心）

```json
{
  "by_claim": {"CLM-001": ["EV-001"]},
  "by_result": {"0": ["EV-001"]},
  "by_sub_question": {"SQ1": ["EV-001"]},
  "orphan_claims": ["CLM-009"],
  "unverified_quotes": [{"evidence_id": "EV-002", "claim_id": "CLM-002", "source_ref": "...", "locator": {...}, "quote_hash": "..."}],
  "verification": {
    "total_claims": 10, "verified_local": 7, "unverified_local": 1,
    "external_unverifiable": 2, "orphan": 1
  }
}
```

- `by_claim` / `by_result` / `by_sub_question`：结论→证据、结果→证据、子问题→证据的索引。
- `orphan_claims`：`relevance_reason`/`screening_reason` 中引用（正则 `CLM-[A-Za-z0-9_\-\.]+`）但无对应证据条目的结论 ID。
- `unverified_quotes`：指向本地材料但规范化后未命中的逐字 quote。
- `verification.orphan` 与 `unverified_local` 大于 0 时，交付前必须复核并写入 `pending_human`。

## screening / sources / uncertainty

- `screening`：`{"included":[], "excluded":[], "uncertain":[], "merged_duplicates":[], "total_seen":N, "notes":"..."}`；`excluded` 每条必带 `record_index/title/reason/evidence_ids`。
- `sources`：`{"databases":[{"name":"paper_search","records":N}], "materials":["materials/paper.md"], "retrieved_on":"ISO8601"}`。
- `uncertainty`：`{"missing_metadata":[], "conflicts":[], "coverage_gaps":[], "notes":[]}`；冲突项需带 `source_conflict` 标注，两个来源都记录不取舍。
