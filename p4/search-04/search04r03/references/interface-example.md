# 最小调用示例（接口示意，非正式案例结果）

> 以下所有 `id`、标题、数值均为**示意占位**，只用于说明接口与目录结构，
> 不冒充任何正式案例的检索结果。正式输入在 `/input`，产物在 `/app/output`。

## 1. 输入目录（示意）

```text
/input/
├── case.json          # 案例元数据，可含 case_id、output_contract、seed
├── task.md            # 任务说明
└── materials/
    ├── seed.pdf       # 种子论文
    └── refs.json      # 数据源给的回引/施引记录（若提供）
```

## 2. 调用方式

```bash
# Step 0（前 3 分钟内）：落盘骨架
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work

# Step 1-2：Agent 按 SKILL.md 增量写回 result.json（每次 append change_log）

# Step 3：生成可复核产物并校验
python3 scripts/main.py finalize --output /app/output --work /app/work

# 提交前自检（不访问网络）
python3 scripts/main.py selftest
```

## 3. 骨架 result.json（示意）

```json
{
  "case_id": "example-case",
  "status": "partial",
  "seed": {"title": "示例种子论文（占位）", "doi": null},
  "seed_key": null,
  "research_question": null,
  "literature_candidates": [],
  "evidence_records": [],
  "missing_note": null,
  "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "limitations": [],
  "errors": [],
  "degradations": [],
  "change_log": [{"time": "…", "content": "bootstrap 写入合法 partial 骨架", "reason": "Step 0"}],
  "runtime": {"started_at": "…", "seconds": 0, "model_requests": 0}
}
```

`output_contract` 的全部字段会按题目原样回填到顶层（此处 `literature_candidates`、
`evidence_records`、`missing_note` 为示意），缺值填 `null`/`[]`，不改名、不删未知键。

## 4. finalize 产物

| 文件 | 说明 |
|-|-|
| `result.json` | 评分主产物 |
| `report.md` | 人类可读复核报告 |
| `papers.csv` | 文献清单（UTF-8-SIG，Excel 可打开） |
| `edges.csv` | 引用边表 |
| `citation_graph.svg` | 确定性分层引用图 |

## 5. 自检结果（示意）

```text
[PASS] 空输入 → insufficient_evidence 且退出码 0
[PASS] 坏 JSON → input_error 且 errors 已记录
[PASS] schema 与 output_contract 回填正确
[PASS] 两次运行 content_hash 与产物逐字节一致
[PASS] 同文不同版本（预印本/期刊版）合并为 1 条并留证据
[PASS] 边方向规范化与最短路径正确
[PASS] 排序两次调用结果一致且种子优先
[PASS] citation_graph.svg 可解析
selftest: 8 passed, 0 failed
```
