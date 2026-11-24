# 中间表示与输出 Schema（可复核约定）

本文件是 `SKILL.md` 第 3、4 节的完整字段说明，供他人或 Agent 复用中间表示与产物结构。所有字段以 JSON 对象描述，缺省值为 `null`（对象）、`[]`（数组）。

## 1. 五层中间表示（/app/work/）

### plan.json —— 意图层
```json
{
  "case_id": "string",
  "research_question": "string|null",
  "seed": "object|null",
  "seed_key": {"type": "doi|arxiv|title_author_year|title", "value": "string"} | null,
  "strategy": {
    "expansion_layers": 3,
    "directions": ["backward", "forward", "co_citation", "bibliographic_coupling"],
    "pruning": {"max_nodes": 60, "exclude_types": [], "min_year": null},
    "stop_criteria": ["string"]
  },
  "coverage_targets": ["string"],
  "paths_to_explain": [{"id": "PATH1", "from": "P1", "to": "P9", "note": "string"}],
  "pruned": [{"id": "string", "reason": "string"}],
  "output_contract": "object|null",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

### paper_registry.json —— 实体层
```json
{
  "clusters": [{
    "canonical_id": "P1",
    "members": [{
      "title": "string", "authors": ["string"], "year": 2021,
      "venue": "string", "type": "journal|conference|preprint|supplement|book|dataset|other",
      "doi": "string|null", "arxiv": "string|null",
      "source": "string", "locator": "string", "quote": "string"
    }],
    "merge_key_used": "doi|arxiv|title_author_year|title|null",
    "merge_evidence": "string",
    "confidence": 0.99
  }],
  "updated_at": "ISO8601"
}
```

### edges.json —— 关系层
```json
{
  "edges": [{
    "id": "E1",
    "source": "P1", "target": "P2",
    "relation": "cites",
    "direction": "backward|forward|co_citation|bibliographic_coupling|unknown",
    "source_label": "string", "target_label": "string",
    "evidence": {"locator": "string", "quote": "string", "source": "string"}
  }],
  "updated_at": "ISO8601"
}
```
方向约定：`source` cites `target`；后向=种子为 `source`，前向=种子为 `target`；只给被引列表不给方向时填 `direction_unknown`，禁止猜测。

### evidence_ledger.json —— 证据层
```json
{
  "entries": [{
    "id": "evt-1",
    "target": "node|edge|cluster|path|numeric|ranking",
    "target_id": "P1|E1|PATH1|null",
    "claim": "string",
    "locator": "materials/x.pdf#p3",
    "quote": "逐字原文",
    "source": "case.json|pdf|crossref|europepmc|...",
    "status": "confirmed|conflict|not_reported"
  }],
  "updated_at": "ISO8601"
}
```

### state.json —— 运行层
```json
{
  "case_id": "string",
  "phase": "skeleton|expanding|finalize|done",
  "started_at": "ISO8601",
  "deadline_seconds": 900,
  "request_budget": 64,
  "requests_used": 0,
  "output_contract": "object|null",
  "input_files": [{"path": "string", "rel": "string", "name": "string", "size": 0}],
  "degradations": ["string|object"],
  "errors": ["string|object"],
  "change_log": [{"time": "ISO8601", "content": "string", "reason": "string"}],
  "pending_human": ["string"],
  "limitations": ["string"],
  "metrics": {
    "papers": 0, "edges": 0, "identity_clusters": 0, "merged_versions": 0,
    "edges_with_quote": 0, "evidence_entries": 0, "pruned_nodes": 0
  }
}
```

## 2. 最终产物 /app/output/result.json

```json
{
  "case_id": "string",
  "status": "ok|partial|insufficient_evidence|input_error",
  "<output_contract 全部字段原样回填>": "…",
  "seed": "object|null",
  "seed_key": "object|null",
  "research_question": "string|null",
  "results": {
    "papers": [{"id": "P1", "versions": [], "merge_key_used": "…", "merge_evidence": "…", "confidence": 0.99, "…元数据"}],
    "edges": [],
    "clusters": [],
    "paths": [{"id": "PATH1", "from": "P1", "to": "P9", "hops": ["P1","P4","P9"], "edges": ["E1","E5"], "kind": "shortest_path|unreachable"}],
    "pruned": []
  },
  "sources": [{"type": "string", "locator": "string", "quote": "string", "note": "string"}],
  "uncertainty": [{"item": "string", "detail": "string", "status": "string"}],
  "change_log": [{"time": "ISO8601", "content": "string", "reason": "string"}],
  "limitations": [], "pending_human": [], "errors": [], "degradations": [],
  "runtime": {"started_at": "…", "finished_at": "…", "status": "…", "budget_seconds": 900, "request_budget": 64},
  "metrics": {"papers": 0, "edges": 0, "identity_clusters": 0, "merged_versions": 0, "evidence_coverage": null}
}
```

`output_contract` 回填规则：保留运行状态字段（`case_id/status/results/sources/uncertainty/change_log/seed/seed_key/research_question/limitations/pending_human/errors/degradations/runtime`）不被契约键覆盖；其余契约键原样回填，值缺失填 `null`/`[]`，不重命名、不删未知键。

## 3. 辅助产物（由 finalize 生成）

- `report.md`：排序表、路径解释、不确定性与缺口、证据覆盖率。
- `papers.csv`：`id,rank,distance,title,year,venue,type,doi,arxiv,authors,merge_key_used,source,locator`。
- `edges.csv`：`id,source,target,relation,direction,source_label,target_label,evidence_locator,evidence_quote`。
- `citation_graph.svg`：确定性分层布局，`source cites target`，箭头指向被引方，种子节点高亮。
