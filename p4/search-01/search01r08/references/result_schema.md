# result.json 字段规范与证据溯源

本文档定义 `scripts/main.py assemble` 产出 `/app/output/result.json` 的字段与核验规则。所有字段均可由 `main.py selfcheck` 与 `scripts/selftest.py` 逐项校验。

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 取自输入的 case_id/challenge_id/armiId，或 task.md 正则提取，最少给 `case:<文件名>` |
| `status` | string | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error`；诚实标注，缺材料不写 `ok` |
| `results` | list | 是 | 候选文献列表，按 rank 升序 |
| `evidence` | list | 是 | 查询/筛选/排序/追踪账本 |
| `coverage_matrix` | list | 是 | 角度 × 计划/执行/命中/状态 覆盖矩阵 |
| `coverage_gaps` | list | 是 | 未覆盖/未执行角度及建议 |
| `sources` | list | 是 | 按数据库聚合的来源统计 |
| `uncertainty` | object | 是 | `missing_metadata` / `conflicts` / `coverage_gaps` / `notes` |
| `pending_human` | list | 是 | 无法自动判断、需人工复核的项 |
| `change_log` | list | 是 | `time / action / reason / status` |
| `research_question` / `search_scope` | 任意 | 建议 | 检索问题与范围说明 |
| `limitations` / `errors` / `degradations` | list | 建议 | 局限与降级记录 |
| `runtime` | object | 建议 | 起止时间、阶段、预算（64 请求 / 900 秒） |

若 `case.json` 含 `output_contract`，其全部键会原样出现在顶层，缺值填 `null`/`[]`，不改名、不删键。

## 2. results 每条字段

```json
{
  "rank": 1,
  "title": "论文标题",
  "authors": ["姓氏, 名", "..."],
  "year": 2023,
  "journal": "期刊名",
  "doi": "10.xxxx/xxxx",
  "url": "https://doi.org/10.xxxx/xxxx",
  "database": "crossref",
  "relevance": 0.92,
  "relevance_reason": "与研究对象直接相关",
  "score": 0.86,
  "score_components": {"relevance": 0.92, "metadata": 1.0, "source": 1.0, "weights": {...}},
  "evidence": [{"locator": "abstract", "quote": "逐字引用片段"}],
  "aspects": ["a1", "a2"],
  "query_ids": ["s1"]
}
```

- `authors` 推荐为字符串数组；若导入为分号/逗号分隔字符串，脚本会正常化为数组。
- `year` 尽量为整数；字符串会被提取首个四位年份。
- `score = 0.5*relevance + 0.25*metadata + 0.25*source`（四舍五入到 4 位）。
  - `relevance`：由 Agent 依据相关性给出 0–1；缺省且有 `relevance_reason` 时取 0.5，否则 0。
  - `metadata`：`title/authors/year/journal/doi` 五字段非空占比。
  - `source`：有 DOI=1.0；URL+数据库=0.8；仅 URL=0.6；数据库+标题=0.5；否则 0.3。
- 排序：`-score` 降序 → `year` 降序 → `title` 规范化升序（确定、可复现）。

## 3. evidence 每条字段（证据账本）

```json
{
  "id": "s1",
  "query": "explainable AI medical imaging",
  "source": "paper_search",
  "database": "crossref",
  "aspect_id": "a1",
  "timestamp": "2026-11-24T10:00:00+0800",
  "rationale": "核心方法术语",
  "kind": "query",
  "hits_found": 12,
  "hits_kept": 1,
  "result_ids": ["10.xxxx/xxxx"]
}
```

- `id` 唯一；未提供时自动分配 `q1, q2, ...`；同 id 再次导入会覆盖（便于修正）。
- `result_ids` 建立“结果 → 查询”正向链接；结合 `aspect_id` 建立“查询 → 角度”反向链接。

## 4. 证据溯源要求（硬要求）

- 每条结果至少一条 `evidence`，含 `locator`（来源定位，如 `abstract`/`page 3`/`query result #1`）与 `quote`（**逐字引用**片段）。
- 无法逐字引用时，`quote` 写 `"not_reported"`，不得编造引文。
- 冲突不隐藏：`uncertainty.conflicts` 记录同一标识下的多来源差异；`source_conflict` 逐条列出。

## 5. status 判定参考

- 有记录、有证据、无覆盖缺口 → `ok`
- 有记录但有缺口或降级 → `partial`
- 无记录（或仅空骨架）→ `insufficient_evidence`
- 输入无法解析 → `input_error`