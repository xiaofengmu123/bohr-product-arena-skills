# 查询构造、去重与排序规则

本文件规定检索召回与结果整理的确定性规则，与 `scripts/core.py` 保持一致。规则目标是提高 Recall 同时控制无关结果，并让每次查询、去重、打分都可复核。

## 1. 多轮查询规划

研究问题拆为四轮，逐步扩大覆盖面并逐步收紧相关性：

| 轮次 | 概念 | 目的 |
|-|-|-|
| 1 | 核心概念直查（题目原词） | 建立候选基线 |
| 2 | 同义词 / 缩写 / 上位词扩展 | 提升 Recall |
| 3 | 引用与被引（reference graph） | 补关键文献、降遗漏 |
| 4 | 数据库交叉核验 | 核 DOI、元数据、去重 |

每轮先跑 `paper_search`（结构化记录，优先 Crossref/EuropePMC/OpenAlex 元数据），结果不理想时用 `web_search` 补检索与元数据核验。

## 2. 关键词扩展核验清单

执行每一轮查询前核对：

1. 核心实体（方法/对象/主题词）是否出现；
2. 是否扩展了同义词、缩写、跨语言表达；
3. 是否覆盖上位/下位概念；
4. 是否加入方法/对象限定词；
5. 是否用否定词或年限控制无关结果。

满足至少 3 项才执行查询；缺项记录到 `plan.keyword_checklist` 的对应说明，写入 `uncertainty.coverage_gaps`。

## 3. 候选去重（upsert）

- **DOI 归一**：小写、剥 `https://doi.org/` 或 `http://dx.doi.org/` 前缀、去首尾空白。
- **标题指纹**：小写、去掉所有非字母数字下划线字符（Unicode 感知），用于无 DOI 时。
- **去重键**：有 DOI 用 `("doi", 归一DOI)`，否则 `("title", 指纹)`；两键相等即视为同一记录，执行合并。

合并策略：取元数据完整度与 relevance 更高者为「主记录」，主记录缺失字段用副记录补齐；evidence 按序列化签名去重后并集保留。合并是确定性操作，重复运行结果一致。

## 4. 透明打分与排序

每条记录 score：

```text
score = 0.6 · relevance + 0.2 · metadata_completeness + 0.2 · source_reliability
```

- `relevance`：Agent 判定的主题相关度（0–1），必须有 `relevance_reason` 支撑；缺失时 0.5（有 reason）或 0.0（无 reason），并在 `pending_human` 标注。
- `metadata_completeness`：`title/authors/year/journal/doi/url` 六项中非空占比。
- `source_reliability`：按来源库映射，Crossref/PubMed/EuropePMC/publisher=1.0，OpenAlex/Scopus=0.95，Semantic Scholar/DBLP=0.9，arXiv/bioRxiv=0.8，预印本=0.7，web_search/web=0.6，未知=0.5。

排序主键：`score` 降序；同分按 `year` 降序；再同按标题指纹降序。rank 从 1 连续编号。脚本把 `score / metadata_completeness / source_reliability` 一并写入每条记录，便于评审还原排序依据。

## 5. 证据溯源

每次检索写一条 evidence：

```json
{"query": "topic X AND method Y", "source": "paper_search",
 "database": "crossref", "timestamp": "2026-11-24T...",
 "rationale": "核心概念直查", "kind": "query", "trace": "第 2 轮"}
```

每条结果的相关性依据写进 `results[].evidence`，逐字引用：

```json
{"locator": "abstract", "quote": "we study topic X ...", "source": "crossref"}
```

评审须能据 `query → database → record → locator+quote` 独立复核每条结果；无法定位的依据不写，改为 `pending_human`。