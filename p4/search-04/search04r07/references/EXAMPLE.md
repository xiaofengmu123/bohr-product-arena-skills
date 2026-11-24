# 最小调用示例（EXAMPLE）

> **本文件所有内容均为示意，用于说明接口与完整流程；不是正式案例结果，不代表任何真实文献。**

## 1. 最小输入

`/input/case.json`：

```json
{
  "case_id": "case-example-001",
  "output_contract": {
    "results": {"type": "object"},
    "seed": {"type": "string"},
    "seed_key": {"type": "string"},
    "research_question": {"type": "string"},
    "pending_human": {"type": "array"}
  },
  "seed_hint": {"title": "Attention Is All You Need"}
}
```

`/input/task.md`（示意）：

```text
# 引文追踪
研究问题：梳理 attention 方法脉络的奠基、后续改进与平行工作。
```

## 2. 调用方式（在 Skill 根目录，相对路径）

```bash
# Step 0：先落盘 partial 骨架（前 3 分钟内必须完成）
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work

# Step 2：把候选写入 work 目录后做身份合并与边重映射
python3 scripts/main.py dedupe --work /app/work

# Step 3：校验 + 生成 report.md / papers.csv / edges.csv / citation_graph.svg
python3 scripts/main.py finalize --output /app/output --work /app/work
```

自检：

```bash
python3 scripts/selftest.py
```

## 3. 示意输出

`/app/output/result.json`（**示例，非正式案例结果**）：

```json
{
  "case_id": "case-example-001",
  "results": {
    "papers": [
      {"id": "P00001", "title": "Attention Is All You Need", "year": 2017,
       "doi": "10.1000/example.seed", "rank": 1,
       "source": {"locator": "materials/x.pdf#p1", "quote": "Attention Is All You Need"}},
      {"id": "P00002", "title": "BERT: Pre-training of Deep Bidirectional Transformers", "year": 2019,
       "doi": "10.1000/example.citing", "rank": 2,
       "source": {"locator": "materials/x.pdf#p2", "quote": "BERT ..."}}
    ],
    "edges": [
      {"source": "P00002", "target": "P00001", "relation": "cites",
       "evidence": {"locator": "materials/x.pdf#p3", "quote": "..."}}
    ]
  },
  "seed": "P00001",
  "seed_key": "doi:10.1000/example.seed",
  "status": "partial",
  "sources": [{"locator": "materials/x.pdf#p1", "quote": "..."}],
  "uncertainty": {"conflicts": [], "missing": ["前向引用未获取"], "coverage_gaps": []},
  "change_log": [{"at": "2026-01-01T00:00:00Z", "what": "bootstrap", "why": "骨架落盘"}],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "limitations": {"missing_data": [], "preview_missing": false},
  "runtime_seconds": 700
}
```

## 4. 与正式案例的边界

- 正式案例由平台以 `/input/case.json` + `/input/task.md` 提供，本示例**不冒充**任何正式结果。
- `status` 必须如实填写；材料不足时降为 `insufficient_evidence`，不补造文献、数值、DOI 或引用。
