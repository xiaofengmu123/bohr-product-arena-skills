# result.json 字段规范与中间表示

本文件规定本 Skill 的两类数据：`/app/output/result.json`（最终交付）与 `/app/work/` 下的中间表示。所有键名稳定，脚本 `assemble` 负责把中间表示合并为最终结果；手工或 Agent 只需写入中间表示，不必直接改 result.json。

## 1. 中间表示（work 目录）

| 文件 | 内容 | 由哪个命令写 |
|-|-|-|
| `inventory.json` | 输入发现结果：文件清单、`case_file`、`task_file`、`materials`、`exports`、`declared_fixtures`、`snapshot_ts`、`degradations` | `scan` |
| `records.json` | **单一记录中间表示**：全部规范化记录（含指纹与去重组） | `scan`、`rank` |
| `plan.json` | 研究问题、子问题、查询计划、术语扩展、停止判断 | `plan` |
| `screening.json` | 逐条筛选账本（键为 record id） | `screen` |
| `evidence.json` | 证据/查询/冲突/缺口日志（数组） | `evidence` |
| `runtime.json` | 运行元数据（快照时间戳、耗时、请求数） | `scan`、`assemble` |
| `errors.json` / `degradations.json` | 边界校验与降级记录 | `scan`、`screen` |
| `rank.json` | 排序后的 record id 列表（可随时由 `rank` 重算） | `rank` |

### records.json 记录字段

```json
{
  "id": "rec-001",
  "fingerprint": "doi:10.1000/abc123 | title:...|smith|2021 | opaque:...",
  "origin":   {"file": "export.json", "path": "/input/...", "database": "export", "kind": "json", "locator": "items[0]"},
  "title": null, "authors": [], "year": null, "journal": null,
  "doi": null, "url": null, "database": "snapshot", "abstract": null,
  "locator": null, "fulltext_locator": "pdf_text/p1.txt",
  "dedup": {"group_id": "g-001", "role": "primary|duplicate|unassigned", "duplicate_of": null, "conflicts": []},
  "screening": {
    "decision": null, "reason": null, "sub_question_ids": [],
    "relevance": null, "relevance_reason": null,
    "evidence": {"locator": null, "quote": null},
    "conflict_with": null, "input_fixture": false
  },
  "score": null, "score_breakdown": null, "rank": null
}
```

- `fingerprint` 优先取 `title:<归一化标题>|<第一作者姓>|<年份>`（标题比 DOI 在重复导出间更稳定）；无标题时取归一化 DOI；都无法归一化时取来源位置哈希 `opaque:...`。
- 同一指纹的记录归入同一 `group_id`，第一条为 `primary`，其余 `role=duplicate` 并指向 `duplicate_of`；组内字段不一致写入各自 `dedup.conflicts`，**不以拍脑袋取舍**。
- 题目声明为输入夹具的记录，由 Agent 在 `screen` 步骤设 `input_fixture=true`。

## 2. result.json 顶层字段

带 `*` 为强制。

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id`* | string | 取自 `case.json` 的 `case_id`/`id`，否则 `case.json` 文件名 |
| `status`* | string | `ok \| partial \| insufficient_evidence \| input_error` |
| `research_question` | string | 案例研究问题原文 |
| `sub_questions`* | array | 子问题拆解，每条含 `id/question/label/object/mechanism/method/time_range/comparison/evidence_requirement` |
| `query_plan`* | array | 查询规划，每条含 `query/database/target_sub_questions/depends_on/parallel/order` |
| `term_expansion`* | array | 术语扩展，每条含 `concept/variants/scope/ambiguity` |
| `stop_decision`* | object | `status(sufficient\|handoff\|marginal)/reason/rounds/criteria/next_action` |
| `results`* | array | 纳入候选（见下） |
| `screening`* | object | 全量账本：`included/excluded/uncertain/merged_duplicates/ledger/totals` |
| `evidence`* | array | 查询/筛选/排序/停止依据，每条含 `seq/timestamp/query/source/database/rationale/kind` |
| `sources`* | array | 输入文件与数据源清单 |
| `uncertainty`* | object | `missing_metadata/conflicts/coverage_gaps/notes` |
| `change_log`* | array | `seq/time/action/reason/status`，`time` 取快照 mtime（幂等） |
| `status_notes` | string | 状态一句话说明 |
| `limitations` | array | 限制与降级 |
| `errors` | array | 解析/校验错误 |
| `degradations` | array | 降级记录（坏编码/超限/缺工具） |
| `pending_human` | array | 需人工补充的具体缺口 |
| `runtime` | object | `started_at/snapshot_ts/wall_seconds/model_requests/token_budget` |
| `selfcheck` | object/null | 自检结果 `passed/total/passed_count` |
| `output_contract` | object/null | 回显案例合同，便于复核 |
| `<合同字段>` | 任意 | `case.json.output_contract` 的每个字段原样回填，缺值给 `null`/`[]` |

## 3. results 数组元素

```json
{
  "rank": 1, "id": "rec-001", "title": "...", "authors": ["..."], "year": 2021,
  "journal": "...", "doi": "...", "url": "...", "database": "snapshot",
  "sub_question_ids": ["SQ1"], "relevance": 0.9, "relevance_reason": "...",
  "screening": "include", "screening_reason": "...",
  "evidence": {"locator": "materials/p1.txt¶3", "quote": "..."},
  "score": 0.84, "score_breakdown": {"relevance": 0.9, "metadata_completeness": 0.83, "source_reliability": 0.9, "weights": {"relevance": 0.6, "metadata": 0.2, "source": 0.2}},
  "fingerprint": "doi:...", "locator": "...", "fulltext_locator": "...",
  "dedup": {...}, "input_fixture": false
}
```

`evidence.locator` 与 `evidence.quote` 为**逐字引用**：locator 指向材料段落（如 `materials/p1.txt¶3`、`export.json:items[0]`），quote 复制原文片段，不得改写。

## 4. 透明打分公式（默认权重）

```
score = 0.6 * relevance + 0.2 * metadata_completeness + 0.2 * source_reliability
```

- `relevance`：Agent 在 `screen` 步骤给出的 0–1 相关性，依据为 `evidence.quote`。
- `metadata_completeness`：title/authors/year/journal/doi/url 六项非空比例，脚本自动计算。
- `source_reliability`：有 DOI → 1.0；snapshot/local/materials → 0.9；export → 0.8；paper → 0.7；web → 0.6；unknown → 0.5。
- 排序：score 降序 → relevance 降序 → year 降序 → title 字典序，同分稳定。

## 5. status 判定

| 条件 | status |
|-|-|
| 无任何可解析记录 | `insufficient_evidence` |
| 有记录但无 included | `insufficient_evidence` |
| `stop_decision.status=handoff` | `partial` |
| 存在覆盖缺口或冲突且未收敛 | `partial` |
| `stop_decision.status=sufficient` 且无缺口 | `ok` |
| 输入不可解析/缺失 | `input_error` |