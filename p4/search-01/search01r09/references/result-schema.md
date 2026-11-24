# result.json 字段与评分规范

本文件定义 `/app/output/result.json` 的结构与确定性打分/排序规则，供 Agent 与复核者对照。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|-|-|-|
| `case_id` | * | 取自输入 case.json / task.md，取不到填 `"unknown"` |
| `status` | * | `ok \| partial \| insufficient_evidence \| input_error`，诚实反映完成度 |
| `results` | * | 候选文献列表（见下） |
| `evidence` | * | 查询与追踪依据列表（见下） |
| `sources` | * | 去重后的来源清单 `{name,database,kind,first_seen}` |
| `status_notes` | * | 空结果、冲突、权限、接口异常说明 |
| `uncertainty` | * | `{missing_metadata, conflicts, coverage_gaps, notes}` |
| `pending_human` | * | 无法自动判断、需人工定夺的事项 |
| `change_log` | * | `{time, action, reason, status}` 的追加式变更记录 |
| `research_question` | | 从输入解析的研究问题 |
| `search_scope` | | 检索范围与使用到的材料 |
| `limitations` | | 已识别的方法/覆盖局限 |
| `errors` | | 硬失败（缺输入、不可读等） |
| `degradations` | | 已恢复的降级（坏编码、超限文件等） |
| `runtime` | | `{assembled_at, record_count, evidence_count}` |
| `output_contract` 的键 | * | 题目契约要求的全部字段，未得到的填 `null`/`[]` |

## 2. results 单条记录

```
{
  "rank": 1,                        # assemble 时按 score 排序后写入
  "title": "...",                   # 真实标题，必需
  "authors": ["..."],               # 尽量齐全；缺失可为 []
  "year": 2021,                     # 缺失 null
  "journal": "...",                 # 缺失 null
  "doi": "10.xxxx/yyyy",            # 规范化（小写、去前缀）
  "url": "https://...",             # 无 DOI 时给出 URL
  "database": "crossref",           # 实际来源库
  "relevance": 0.9,                 # 0-1 相关性判断
  "relevance_reason": "...",        # 相关性依据（不可为空）
  "score": 0.84,                    # 脚本计算，见第 4 节
  "metadata_completeness": 0.8,     # 脚本计算
  "source_reliability": 1.0,        # 脚本计算
  "evidence": [{"locator": "...", "quote": "..."}],  # 逐字溯源，必需
  "merged_from": ["..."]            # 去重时被合并的重复标题，可选
}
```

`evidence` 的 `locator` 指向可复核位置（如 `paper_search#3`、`materials/x.pdf#p2`、
`https://doi.org/...`）；`quote` 必须为来源中的逐字片段，不得改写。

## 3. evidence 单条查询依据

```
{
  "query": "graph neural network protein interaction",
  "source": "paper_search",         # paper_search / web_search / crossref / ...
  "database": "crossref",           # 数据来源库
  "kind": "query",                  # query / query_expansion / filter / dedup / ranking / trace
  "rationale": "概念种子查询",
  "scope": "",
  "timestamp": "2026-11-24T..."
}
```

## 4. 透明打分

- `metadata_completeness` = 非空字段数 / 5（title, authors, year, journal, doi）。
- `source_reliability`：crossref/europepmc/pubmed/publisher/doi=1.0；arxiv/biorxiv/medrxiv=0.9；
  openalex/semantic_scholar=0.85；paper_search=0.8；web_search=0.7；materials/pdf=0.7；未知=0.6。
- `relevance` 由 Agent 判断（0-1）；未提供时脚本默认 0.5 中性值。
- `score = round(0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability, 4)`。
- 排序键：`score` 降序 → `year` 降序 → `title` 升序；rank 从 1 起。

## 5. status 判定

- 无任何输入文件 → `input_error`。
- 有输入但零候选 → `insufficient_evidence`。
- 有候选且无错误 → `ok`；有候选但有硬错误 → `partial`。
- 骨架/显式 `--status partial` 阶段固定为 `partial`。
- 缺元数据不改变 status，只写入 `uncertainty.missing_metadata` 与字段 `null`。
