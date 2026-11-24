# result.json 输出契约与字段规范

本文件是 `/app/output/result.json` 的字段契约，供 Agent 在 `import` / `assemble` 时遵守，也供评审复核。

## 顶层字段

| 字段 | 必需 | 类型 | 说明 |
|-|-|-|-|
| `case_id` | 是 | string | 取自 `/input/case.json`（`case_id`/`id`/`armiId`/`challenge_id`），缺失时用文件名占位 |
| `status` | 是 | enum | `ok`\|`partial`\|`insufficient_evidence`\|`input_error`，必须诚实 |
| `research_question` | 否 | string | 研究问题，取自 case.json 或 task.md |
| `search_scope` | 否 | list | 查询阶梯（概念种子→关键词扩展→AND 组合→引文追踪） |
| `results` | 是 | list | 通过证据闸门的候选文献（带 rank、元数据、score、evidence） |
| `evidence` | 是 | list | 三层溯源：query / source / relevance（每条带 locator+quote） |
| `sources` | 是 | list | 唯一来源清单（source/database/kind/url） |
| `uncertainty` | 是 | object | 显式不确定性：missing_metadata / conflicts / coverage_gaps / verification / notes |
| `limitations` | 是 | list | 未完成、未核验、覆盖缺口的诚实说明 |
| `pending_human` | 是 | list | 未达证据闸门的候选（缺失标题/来源/逐字引用），不作为正式结果 |
| `errors` | 是 | list | IO/解析错误 |
| `degradations` | 是 | list | 降级记录（坏编码、坏 JSON、超限文件等） |
| `status_notes` | 是 | list | 空结果、冲突、异常、权限说明 |
| `change_log` | 是 | list | 每次动作：time/action/reason/status |
| `runtime` | 是 | object | started_at / finished_at / seconds_elapsed |
| `<contract 字段>` | 是 | 任意 | 题目 `output_contract` 的**全部字段**原样回填，缺失给 `null`/`[]` |

## results[] 条目

```json
{
  "rank": 1,
  "record_id": "9f3c…",
  "title": "…",
  "authors": ["…"],
  "year": 2016,
  "journal": "…",
  "doi": "10.…/…",
  "url": "https://…",
  "database": "crossref",
  "relevance": 0.95,
  "relevance_reason": "…",
  "score": 0.87,
  "verification_status": "verified | partial",
  "meta_verified": true,
  "meta_confidence": 0.9,
  "evidence": {"locator": "…", "quote": "逐字片段", "source": "…"}
}
```

## evidence[] 三类

- `kind="query"`：一次检索查询，记录 `query`/`source`/`database`/`results_count`/`rationale`。
- `kind="source"`：一个来源（数据库/网页/本地材料）的定位与可复核 URL。
- `kind="relevance"`：一条候选的相关性依据，**必须**有 `locator`（定位至页面/段落/记录）与 `quote`（逐字原文片段）。

## 打分规则（确定性、可复现）

```
score = 0.6·relevance + 0.2·元数据完整度 + 0.2·来源可靠度
元数据完整度 = (authors + year + journal + doi + url 命中数)/5 ×（元数据未核验时 ×0.5）
来源可靠度 = DATABASE_TRUST[database]（crossref/pubmed=0.90、arxiv=0.85、web=0.60、unknown=0.50）
```

排序：`score` 降序 → `year` 降序 → `title` 升序。同输入重复运行结果一致。