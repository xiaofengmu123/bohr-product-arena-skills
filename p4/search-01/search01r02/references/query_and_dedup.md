# 查询构造、去重与排序规则（确定性约定）

本文件规定 `scripts/main.py` 在召回、去重、打分、排序阶段的具体规则，
保证同一输入重复运行得到一致的研究结果。

## 1. 多轮查询构造

1. **单概念召回**：从研究问题中切分核心概念（按 `，,;；、` 与换行切分），
   每个概念独立查询，确认基础命中面。
2. **AND 组合收紧**：核心概念用 `AND`（或空格/加号）组合，提高 Precision。
3. **同义词/缩写/跨语言扩展**：为每个核心概念列出同义词、缩写、英文/中文
   对应词与上位/下位概念，写入 `plan.json` 的 `keyword_checklist`，逐条核验
   是否已检索；控制无关结果，不无限扩展。
4. **引文追踪**：对高相关记录的引用与被引关系补充召回，减少关键文献遗漏。

所有查询通过 `main.py evidence` 逐条登记（query/source/database/rationale），
评审者可据此复现每次检索。

## 2. 候选记录规范化

导入时接受任意键名（title/name/paper_title、authors/author、doi/DOI、
url/link、year/publication_year、journal/venue/container/publisher 等），
收敛为统一结构；缺少 `title` 的记录丢弃并记录 `skipped:empty_title`。

## 3. 去重

- 去重键优先级：规范化 DOI（小写、去掉 `https://doi.org/`、`doi:` 前缀）
  > 规范化标题哈希（标题小写后仅保留字母与数字，SHA-1 前 16 位）。
- 同一键再次导入时做 upsert 合并：已有非空字段保留，仅用新字段填补空缺；
  证据去重后追加。
- 不同数据库返回同一文献时，保留先导入记录的元数据，新来源证据并入。

## 4. 透明打分

对每条记录计算三部分得分，权重固定、可复核：

```
score = 0.6 · relevance + 0.2 · completeness + 0.2 · provenance
```

- `relevance`：0–1 相关性判定（由 Agent 依据证据给出）；未给出时按 0.5 记入
  并在 `uncertainty.notes` 标注 `relevance_default_0.5`。
- `completeness`：`title/authors/year/journal/doi` 五项中非空的比例。
- `provenance`：来源可靠度档位——crossref/europepmc/pubmed/publisher/doi=1.0；
  arxiv/semanticscholar/openalex/dblp/biorxiv/medrxiv=0.9；
  google_scholar/aggregator=0.7；generic/web=0.5；unknown=0.3。

## 5. 确定性排序

按以下键依次排序（无随机因素）：

```
score 降序 → year 降序 → title 小写字典序 → DOI 规范化字典序
```

`rank` 由排序结果连续赋 1..N。

## 6. 预算与台账

- 阶段预算：mint 180s/2 请求、plan 90s/3 请求、recall 420s/40 请求、
  verify 120s/8 请求、assemble 60s/1 请求、buffer 30s/10 请求，
  合计 900s/64 请求。
- Agent 在每次模型请求前调用 `main.py ledger` 登记；脚本累计请求数并在
  超过 64 次时标记 `over_budget`，超过 76 次返回 `STOP_BUDGET_EXHAUSTED`
  并退出码 3，提示停止追加、密封当前结果。
