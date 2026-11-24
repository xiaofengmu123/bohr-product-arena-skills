# result.json / evidence_map 规范

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取自输入；找不到用 `case:<文件名>` 兜底 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，诚实标注 |
| `research_question` | string\|null | 研究问题 |
| `search_scope` | object | 输入目录、命中数据库集合 |
| `results` | array | 候选文献，按 score 降序、标题字典序稳定排序 |
| `evidence` | array | 查询/来源记录（kind/query/source/database/locator/quote/rationale/timestamp） |
| `evidence_map` | object | 更细的证据映射，键为 `EV0001..`，见下 |
| `sources` | array | 来源统计（name/type/database/access_count） |
| `uncertainty` | object | `missing_metadata` / `conflicts` / `coverage_gaps` / `notes` |
| `status_notes` | array | 空结果、冲突、接口异常等状态说明 |
| `change_log` | array | `time/action/reason/status` 全程追加 |
| `limitations/errors/degradations/pending_human` | array | 缺失、失败、无法判断分别记录 |
| `runtime` | object | `started_at/updated_at/elapsed_seconds/script_version` |

## 2. results 每条

```json
{
  "rank": 1,
  "title": "…", "authors": ["…"], "year": 2023, "journal": "…",
  "doi": "10.xxxx/yyyy", "url": "https://doi.org/…", "database": "crossref",
  "relevance": 0.9, "relevance_reason": "…",
  "score": 0.82,
  "evidence": [{"kind": "relevance", "locator": "case.json#research_question",
                "quote": "…逐字片段…"}],
  "evidence_ids": ["EV0002"]
}
```

- `evidence` 是**逐条**依据：每项必须含 `locator`（`文件#定位` 或 `database#标识`）与 `quote`（逐字片段）；缺失时置 `null` 并进入 `uncertainty.missing_metadata`。
- `evidence_ids` 由 assemble 自动回链到 `evidence_map` 的键，形成双向可追溯。

## 3. evidence_map（本变体强化点）

键 `EV0001..`，每条结构：

```json
{
  "kind": "query | evidence | relevance | verify | dedup | conflict | gap",
  "claim": "该证据支撑的结论（人话）",
  "locator": "精确到文件/段/DOI 的位置",
  "quote": "逐字原文片段（缺失则 null）",
  "source": "case.json | task.md | materials/... | paper_search | crossref | …",
  "database": "crossref | europepmc | publisher | …",
  "rationale": "为何该证据支持该结论",
  "supports": ["result#1", "research_question", "uncertainty"],
  "timestamp": "ISO8601"
}
```

- `supports` 是反向链接：任何结论都能被“谁支持我”反向检索。
- 冲突/遗漏各入一条 `conflict` / `gap`，不隐藏。

## 4. output_contract 回填规则

- `parse` 从 case.json 的多组键名（`output_contract`/`output`/`schema`/`required_fields` 等）自动发现字段名，写入 `case_summary.json#contract_fields`。
- `assemble` 把这些字段原样回填进 result.json：已有值保留，缺值填 `null`/`[]`，不改名、不删键。
- 未提供 `output_contract` 时，本 Skill 内置 `case_id/status/results/evidence/evidence_map/sources/uncertainty/change_log` 等强制字段，同样满足题目“检索结果/证据记录/状态说明”三要求。

## 5. 最小完整示例（示意，非正式案例）

输入 `case.json` 提供研究问题与 `output_contract`：

```json
{"case_id": "case-demo", "research_question": "CRISPR 在心血管基因治疗中的递送策略",
 "output_contract": {"required": ["target_papers"]}}
```

一次 `import --inline`（单条候选）+ 一次 `evidence --inline`（查询）后，`assemble` 产出关键片段：

```json
{
  "case_id": "case-demo",
  "status": "partial",
  "results": [{
    "rank": 1, "title": "Cationic lipid nanoparticles deliver CRISPR for cardiomyopathy",
    "authors": ["A Smith", "B Lee"], "year": 2023, "journal": "Nature",
    "doi": "10.1000/xyz123", "database": "crossref", "relevance": 0.9,
    "score": 0.86, "evidence_ids": ["EV0002"]
  }],
  "evidence_map": {
    "EV0001": {"kind": "query", "claim": "单概念召回查询", "locator": "plan.json#round1",
                "quote": "CRISPR 心血管 递送", "source": "paper_search",
                "database": "crossref", "supports": ["research_question"]},
    "EV0002": {"kind": "evidence", "claim": "候选文献与题目直接相关",
                "locator": "case.json#research_question",
                "quote": "CRISPR 在心血管基因治疗中的递送策略",
                "database": "crossref", "supports": ["result#1"]}
  },
  "target_papers": ["Cationic lipid nanoparticles deliver CRISPR for cardiomyopathy"],
  "uncertainty": {"missing_metadata": [{"record": "…", "field": "authors"}],
                  "conflicts": [], "coverage_gaps": [], "notes": []},
  "change_log": [{"time": "…", "action": "import", "reason": "…", "status": "partial"}]
}
```

- `evidence_map` 反向链接让“result#1 为何入选”能沿 `evidence_ids` → `EV0002` → `locator` → `quote` 一路复核到底。
- `target_papers` 是题面 `output_contract` 字段，原样回填；未获得的字段给 `null`。
