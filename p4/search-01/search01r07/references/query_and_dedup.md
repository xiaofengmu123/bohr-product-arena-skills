# 查询规划、去重与排序规则

本文档规定可复现的检索流程，供 Agent 执行与人工复核。核心原则：**先规划、多轮召回、逐条留证据、确定性排序**。

## 1. 多轮查询规划（4 轮）

`scripts/main.py plan` 生成初始 plan.json；Agent 可据案例改写。每轮都写入 `evidence.json`。

| 轮次 | 类型 | 目的 | 触发工具 |
|-|-|-|-|
| 1 | 单概念召回 | 每个核心概念单独检索，建立主干候选 | `paper_search` / `web_search` |
| 2 | AND 组合 | 核心概念两两 AND，提高 Precision | `paper_search` |
| 3 | 同义词/缩写/跨语言扩展 | 补 Recall、控制无关结果 | `paper_search` / `web_search` |
| 4 | 引文与本地增量导入 | 引用追踪 + `/input`、`/app/work` 中已有材料导入 | `main.py import` |

## 2. 关键词扩展核验清单

- 专业术语与规范名（MeSH/学科惯用名）；
- 同义词（含口语/书面差异）；
- 缩写展开（如 "NLP" → "natural language processing"）；
- 跨语言表达（中英术语对照，检索库语言不限）；
- 上下位概念与相关方法名（研究对象、方法、指标）；
- 否定与限定词（inclusion/exclusion），用于过滤不相关结果。

## 3. 候选导入与去重

`main.py import --file <path>` 支持 JSON 对象/数组、JSONL、CSV。

去重 fingerprint 优先级：

1. `d:<规范化DOI>`（去掉 `https://doi.org/`、`doi:` 前缀，小写化）；
2. `t:<规范化标题>[@年份]`（NFKC 归一化、去标点、去 stopword、空白折叠）。

同 fingerprint 的候选合并：优先回填缺失元数据，作者与证据取并集，来源标到 `_merged_from`。

## 4. 透明打分

```
score = 0.6·relevance + 0.2·metadata_completeness + 0.2·source_reliability
```

- `relevance`：Agent 给出的 0–1 相关性分；
- `metadata_completeness`：title/authors/year/journal/doi/url 六项中非空占比；
- `source_reliability`：DOI + 可信库（crossref/europepmc/pubmed/publisher）=1.0；DOI=0.85；可信 URL=0.75；其他 URL=0.55；无来源=0.15。

排序：`score` 降序 → `year` 降序 → 标题升序（确定性，重跑一致）。

## 5. 证据记录（evidence.json）

每条至少含 `query / source / database / rationale / results_count`；`timestamp` 自动补。同一查询可被他人按 `source + query` 复现。

## 6. 遗漏说明

召回后说明未覆盖方向：`uncertainty.coverage_gaps`。无结果不编造：`status=insufficient_evidence`，`limitations` 写明下一步检索建议。
