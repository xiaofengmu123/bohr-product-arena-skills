# 查询构造、去重与追踪规则

本文件给出检索执行时的确定性规则，供 Agent 依据 `scripts/main.py plan` 输出的计划执行并回填 `evidence`。

## 1. 多轮查询规划

按四轮推进，每轮都写入 evidence（kind 区分）：

1. **概念种子**（kind=`query`）：把研究问题拆成核心概念词，逐个用 paper_search 召回，建立候选池。
2. **关键词扩展**（kind=`query_expansion`）：对每个核心词列出缩写/全称/同义词/上下位概念/中英表达，
   用 `A OR B` 形式扩展，扩大 Recall 同时用主题词过滤噪声。
3. **AND 组合**（kind=`query`）：把 2-3 个强主题词用 `AND` 组合，收紧相关性。
4. **引文追踪**（kind=`trace`）：从高相关记录的参考文献或被引记录回追关键文献，补遗漏。

每轮至少记一条 evidence：查询串、工具/库、目的（rationale），保证他人可按记录复现。

## 2. 关键词扩展核验清单

- [ ] 专业术语 → 缩写 → 全称（如 GNN ↔ Graph Neural Network）
- [ ] 同义词 / 近义表达（如 prediction ↔ inference ↔ forecasting）
- [ ] 上下位概念（宽/窄：drug ↔ kinase inhibitor）
- [ ] 方法名 / 模型名 / 数据集名（如 AlphaFold、STRING、DIP）
- [ ] 跨语种表达（中英对应词）

## 3. 跨库召回与可靠性分层

优先顺序与 `source_reliability` 分层一致：crossref / europepmc / pubmed / 出版社 DOI 页面 →
arxiv / biorxiv / medrxiv → openalex / semantic_scholar → paper_search / web_search → 本地 PDF/materials。
多库返回同一文献时合并记录并保留可靠性更高的元数据。

## 4. 去重规则

- DOI 归一化（小写、去 `https://doi.org/` 前缀、去尾随标点）后相等即判重复。
- 无 DOI 时用**规范化标题**：NFKC 归一化 → 小写 → 非字母数字与 CJK 字符替换为空格 → 压缩空白。
- 命中重复时 upsert：优先保留非空字段；合并 evidence（按 locator+quote 去重）；原标题追加进 `merged_from`。
- 同一指标多来源不一致时**不擅自取舍**，全部保留并写入 `uncertainty.conflicts`（标 source_conflict）。

## 5. 相关性判断与遗漏说明

- 每条结果必填 `relevance_reason`：写明“为什么与本研究问题相关”，不写空话。
- 筛选掉的主题方向与可能遗漏的关键文献写入 `uncertainty.coverage_gaps`；
  无法判断的相关性写入 `pending_human`。
- 检索为空时结果 `insufficient_evidence`，在 `limitations` 给出下一步建议（换关键词、换库、引文追踪），**不补造文献**。

## 6. 证据溯源要求（硬性）

- 每条 results 至少一条 `evidence`，含 `locator` + 逐字 `quote`。
- 每条 evidence（查询日志）含 `query`、`source`、`database`、`rationale`、`kind`、`timestamp`。
- 所有来源写入 `sources`；任何关键结论都能从 `evidence` 反向定位到原始查询与来源。
