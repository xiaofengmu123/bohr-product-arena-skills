# result.json 字段规范（科研问题驱动检索）

> 本文是字段级契约与中间产物说明，供 Agent 填写和他人复核使用。字段名若与
> `/input/case.json` 的 `output_contract` 冲突，以运行时契约为准：**契约键必须
> 原样回填，不改名、不删除**；本文列出的 `sources` / `uncertainty` / `change_log`
> 等是统一信封，始终追加。

## 1. 根字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从输入原样带出，禁止覆盖 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `research_question` | string\|null | 从 case/task 识别的完整研究问题 |
| `sub_questions` | array | 六要素拆解，每条见 §2 |
| `query_plan` | array | 依赖化查询计划，每条见 §3 |
| `term_expansion` | array | 受控术语扩展，每条见 §4 |
| `stop_decision` | object | 停止判断，见 §5 |
| `results` | array | 最终纳入的候选（screening=include），每条见 §6 |
| `screening` | object | 全量筛选账本，见 §7 |
| `evidence` | array | 逐轮查询/筛选/冲突/缺口日志，见 §8 |
| `sources` | array | 检索来源汇总（source/database/query_count/record_count） |
| `uncertainty` | object | `missing_metadata[]` / `conflicts[]` / `coverage_gaps[]` / `notes[]` |
| `change_log` | array | `time/action/reason/status` 增量留痕 |
| `status_notes` | array | 状态解释（缺拆解、uncertain 数量、合并重复、无 included 等） |
| `limitations` / `errors` / `degradations` / `pending_human` | array | 限制、异常、降级、需人工项 |
| `runtime` | object | 运行元信息（seconds / model_requests_used / phase） |

## 2. 子问题 sub_questions[]

```json
{
  "id": "SQ1",
  "facet": "mechanism | object | method | time_range | comparison | evidence_req",
  "text": "可检索的自然语言子问题",
  "key_concepts": ["核心概念"],
  "required_evidence": "研究类型/结局指标/样本量等证据要求",
  "parent": null
}
```

拆解要求：识别研究对象、机制、方法、时间范围、比较关系、证据要求六个要素；每个子问题必须可独立检索。

## 3. 查询计划 query_plan[]

```json
{
  "step": 1,
  "purpose": "为什么检索",
  "query_template": "具体查询式",
  "concept_ids": ["SQ1 或概念 id"],
  "depends_on": ["前置步骤"],
  "parallel_group": "A",
  "source": "paper_search | web_search | 本地材料"
}
```

依赖说明：`depends_on` 为空表示可并行；非空表示需等待前置查询结果。

## 4. 术语扩展 term_expansion[]

```json
{
  "concept": "CRISPR",
  "variants": [{"term": "clustered regularly interspaced short palindromic repeats", "type": "full-form"}],
  "rationale": "缩写展开，提高召回",
  "max_variants": 6
}
```

发散控制：每个概念变体 ≤6，变体类型包括 synonym / abbreviation / full-form / cross-language / hypernym / hyponym / domain-variant。

## 5. 停止判断 stop_decision

```json
{
  "status": "pending | sufficient | low_marginal_return | handoff",
  "reason": "为什么停或为什么转人工",
  "subquestion_coverage": {"SQ1": 2},
  "idle_rounds": 0
}
```

阈值（`plan.json` 的 `stop_thresholds`）：每子问题 ≥1 条 included 证据；每轮 ≥1 条新相关；连续空转 ≤2 轮。

## 6. 结果 results[]

```json
{
  "rank": 1,
  "title": "逐字标题",
  "authors": ["作者"],
  "year": 2021,
  "journal": "期刊",
  "doi": "10.xxxx/xxxx",
  "url": "来源 URL",
  "database": "来源库",
  "sub_question_ids": ["SQ1"],
  "relevance": 0.98,
  "relevance_unset": false,
  "relevance_reason": "相关性理由",
  "screening": "include",
  "screening_reason": "筛选理由",
  "evidence": [{"locator": "abstract#para1", "quote": "逐字片段"}],
  "score": 0.93,
  "score_breakdown": {"relevance": 0.98, "metadata_completeness": 1.0, "source_reliability": 0.9, "relevance_unset": false}
}
```

排序：included 在前，按 `score` 降序。`score = 0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability`，可复算。`score` 是 Skill 自身的排序依据，**不冒充组织方评测分数**。

## 7. 全量筛选账本 screening

```json
{
  "included": [],
  "excluded": [],
  "uncertain": [],
  "merged_duplicates": []
}
```

全量口径：候选记录每一条必须落入 included / excluded / uncertain 之一；排除项与待定项**不静默丢弃**；重复项（DOI/规范化标题）合并后记入 `merged_duplicates`。

## 8. 证据日志 evidence[]

```json
{
  "kind": "query | round | conflict | gap | screening | note",
  "round": 1,
  "query": "具体查询式",
  "source": "paper_search",
  "database": "PubMed",
  "timestamp": "ISO 时间",
  "rationale": "为什么这样查",
  "record_id": null
}
```

要求：每轮查询、每条筛选决定、每个冲突与缺口都应可回溯到这里的日志或 `records.json`。

## 9. 中间产物（/app/work/）

| 文件 | 内容 |
|---|---|
| `case_summary.json` | case_id、研究问题、约束、`output_contract`、材料清单、degradations |
| `plan.json` | 拆解、术语扩展、查询计划、停止判据与阈值 |
| `records.json` | 规范化候选账本（全量记录，含 screening 与证据） |
| `evidence.json` | 逐轮证据日志 |
| `change_log.json` | 增量操作留痕 |
| `papers.json` | 可选：题目给定的有限论文/查询快照 |

`assemble` 只读上述中间产物，确定性组装 `result.json`，保证可复核与幂等。

## 10. 证据溯源纪律

1. `quote` 必须是来源材料中**逐字可见的片段**，不得改写标点或跨段拼接。
2. 每个关键结论都要有 `locator + quote`；找不到原文支持的结论写 `inferred` 或 `pending_human`，不要硬编 quote。
3. 同一指标多来源不一致：全部保留并标 `source_conflict`（写入 `evidence` kind=conflict），不擅自取舍。
4. 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`。
