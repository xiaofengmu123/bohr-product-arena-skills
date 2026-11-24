# result.json 字段规范（references/result-schema.md）

本文件定义 `/app/output/result.json` 的字段契约，供 Agent 在写入 `plan_in.json`、`records_in.json`、`evidence_in.json` 时逐字段对照。所有 `*` 字段为强制；脚本在 `scaffold` 阶段即写出含全部强制键的 partial 骨架，后续增量填值。除 `runtime` 的 `elapsed_seconds`/`model_requests` 由 Agent 通过 `assemble --runtime-seconds/--runtime-requests` 如实回填外，脚本自身**不写入任何检索结论、文献元数据、评分或来源**。

## 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` * | string | 来自输入；缺失回填 `not_reported` 并记 `missing_metadata` |
| `status` * | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `research_question` | string \| null | 从 case.json / task.md 识别的研究问题 |
| `sub_questions` * | array | 六要素拆解（见下） |
| `query_plan` * | array | 查询顺序与依赖 |
| `term_expansion` * | array | 术语扩展 |
| `stop_decision` * | object | 停止判断 |
| `results` * | array | 纳入候选（include + uncertain），按 score 降序排名 |
| `screening` * | object | 全量筛选账本 |
| `evidence` * | array | 证据动作日志 |
| `sources` * | array | 输入文件与来源清单 |
| `uncertainty` * | object | 缺失/冲突/缺口/备注 |
| `change_log` * | array | 增量变更记录 |
| `status_notes` | string \| null | 状态一句话说明 |
| `limitations` | array | 明确局限 |
| `errors` | array | 解析/接口错误 |
| `degradations` | array | 降级事项（坏 JSON/坏编码/超大文件/缺软件） |
| `pending_human` | array | 需人工补充的缺口 |
| `runtime` | object | 预算与实测 |
| `scoring` | string | 打分公式说明 |
| `output_contract` * | object | 题目契约原样回填（无则 `{}`） |
| `<contract 字段>` | 任意 | 契约各字段展平回填，缺值 `null`/`[]` |

## sub_questions 元素（六要素，缺一不可）

```json
{"id": "SQ1", "question": "...", "object": "...", "mechanism": "...",
 "method": "...", "time_range": "...", "comparison": "...",
 "evidence_requirement": "..."}
```

- `object` 研究对象；`mechanism` 作用机制/通路；`method` 研究方法/技术路线；`time_range` 时间范围（无限制写 `no_restriction`）；`comparison` 比较关系（无对照写 `none`）；`evidence_requirement` 证据要求（如 RCT / 综述 / 原始实验数据）。
- 若某要素在题目中不存在，写 `none` 或 `no_restriction`，**不得省略字段**，否则 selfcheck 的 `sub_questions_6` 会 FAIL。

## query_plan 元素（依赖化）

```json
{"step": 1, "query": "...", "database": "paper_search",
 "depends_on": [], "parallel_with": [], "rationale": "..."}
```

- `step` 正整数；`depends_on` 前置步骤编号列表；`parallel_with` 可并行的步骤编号；`database` 为 `paper_search` / `web_search` / 本地 `materials`。
- 原则：先对象+机制，再方法，最后时间/比较约束；依赖只允许指向更小 `step`，避免成环。

## term_expansion 元素（受控发散）

```json
{"concept": "...", "variants": ["..."], "cross_language": ["..."],
 "hypernym": "...", "disambiguation": "...", "expansion_limit": 6}
```

- 每概念 `variants` 总数 ≤ `expansion_limit`（默认 6）；歧义必须写 `disambiguation`，否则拆两条查询分别记录。
- 保留缩写、同义词、上下位与跨语言表达，但**不引入无关领域变体**。

## results 元素（纳入候选）

```json
{"rank": 1, "title": "...", "authors": ["..."], "year": 2024,
 "journal": "...", "doi": "...", "url": "...", "database": "paper_search",
 "sub_question_ids": ["SQ1"], "relevance": 0.9, "relevance_reason": "...",
 "screening": "include", "screening_reason": "...",
 "evidence": {"locator": "materials/p.pdf#p3", "quote": "逐字引文"},
 "score": 0.76, "score_breakdown": {"relevance": 0.9, "metadata_completeness": 0.85, "source_reliability": 0.8}}
```

- `rank`/`score`/`score_breakdown` 由 `assemble` 计算，Agent 可不填。
- `relevance` ∈ [0,1]，为 Agent 判断；必须伴随 `relevance_reason` 与 `evidence.locator + quote`（逐字），否则视为空泛。
- `evidence.quote` 必须逐字来自来源，不得改写或补写。

## screening 对象（全量账本）

```json
{"total_records": 2, "included": [...], "excluded": [...],
 "uncertain": [...], "merged_duplicates": [...]}
```

- 四桶记录数之和 ≥ `total_records`；`excluded`/`merged_duplicates` 每项必须带 `screening_reason`，**排除项不静默丢弃**。

## evidence 元素（动作日志）

```json
{"query": "...", "source": "paper_search", "database": "paper_search",
 "timestamp": "...", "rationale": "...", "kind": "search"}
```

- `kind` ∈ search / screening / ranking / stop / conflict_check；每轮查询、筛选、排序、停止动作各记一条。

## uncertainty 对象

```json
{"missing_metadata": [...], "conflicts": [...], "coverage_gaps": [...], "notes": [...]}
```

- 元数据缺失 → `missing_metadata`；同一指标多来源不一致 → 全部记录并标 `source_conflict`；未覆盖子问题 → `coverage_gaps`（由 `assemble` 自动补齐）。

## change_log 元素

```json
{"time": "...", "action": "...", "reason": "...", "status": "partial"}
```

- 每条增量命令自动追加；`time` 取源文件 mtime（固定快照，保证同输入重复运行确定）。

## runtime 对象

```json
{"budget_seconds": 900, "budget_requests": 64,
 "elapsed_seconds": null, "model_requests": null}
```

- `budget_*` 为固定预算；实测值仅由 Agent 通过命令行参数回填，脚本不猜测、不编造。
