# 方法规则（references/method.md）

本文是 `SKILL.md` 的可执行方法细则，供 Agent 与第三方复核者使用。核心原则：**每条结论都要有 locator + 逐字 quote，证据链可被独立重建**。

## 1. 从问题到证据的闭环

1. 读题：从 `case.json`/`task.md` 提取研究问题、源语言、目标语种、检索范围与已知材料。
2. 术语对齐：对每个概念列源词、语种、变体（同义词/缩写/上下位/音译/本地译名）、类型、依据与验证方式。依据必须是材料或检索来源中的逐字片段。
3. 逐语种查询：按目标语种构造查询，记录翻译服务与版本、原文-译文对照。不得只用母语查询。
4. 检索：用 web_search / paper_search 分别检索各语种，逐条记录命中。
5. 语言/版本判定：依据正文语言、摘要语言、双语刊声明、出版方翻译声明来标注，不以标题或文件名臆断。
6. 去重：先按规范化 DOI，再按规范化标题归并；同组标 canonical / duplicate_of 并写依据。
7. 误译复核：对高风险命中做回译或人工抽检，识别“词面对应、语义不同”的假阳性并记录判定。

## 2. 证据链（evidence_map）写入规则

- 一条结论一条证据：`claim` 一句话，`claim_type` 限定类别，`target` 可选链到 `results[i]`。
- `locator.source` 取值：`material`（输入材料）、`web`（网页）、`paper_index`（paper_search 记录）、`publisher_page`（出版方页面）。
- `locator.path` 给精确位置：`materials/a.pdf#p3`、URL 或 DOI；`locator.offset` 补充段落/页/行。
- `quote` 必须是 locator 指向处的**逐字复制**，不改写、不概括；`quote_language` 标注该片段语种。
- `quote_hash` = `hash_text(quote)`（sha1 前 12 位），用于第三方核对是否被改动。
- `basis` 写采信理由；`verification` 写他人如何复核；`confidence` 与证据强度一致。
- 结果条目带 `language`/`version`/`relevance` 之一时，必须有一条 `target.results_index == i` 的证据，否则 `main.py validate` 报错。

## 3. 语言与版本判定的证据来源

| 结论 | 必须的证据 | 示例 quote |
|---|---|---|
| 正文为葡萄牙语原文 | 材料正文/出版方页面出现葡语段落 | "resumo"、"palavras-chave" |
| 正文为英语原文 | 材料正文为英文全文 | "This study investigated..." |
| 出版方全文翻译 | 出版方页面声明“full translation”/双语版本 | "English translation of the original" |
| 仅英文摘要 | 材料只有 abstract，无全文 | "Abstract"（无 Methods/Results 正文） |
| 精确重复 | 同 DOI 或同规范化标题出现两次 | 两边 locator 并存 |

判定依据不足时标 `undetermined` / `abstract_only`，不得冒充全文。

## 4. 去重规则

1. 规范化 DOI：去前缀 `https://doi.org/`、`dx.doi.org/`，小写化；相同则同篇。
2. 无 DOI 时用 `normalize_title`：NFKD 去变音、去标点、小写化；相同则视为同篇候选，需再人工确认。
3. 同组保留一条 `canonical`，其余标 `duplicate_of` 指向 canonical，`basis` 写“相同 DOI”或“标题规范化一致 + 双方 locator”。
4. 原文与译文是同一论文的两个版本，只计一次；`dedup` 记录双方 locator 与翻译方向。

## 5. 诚实与降级边界

- 不编造引用、DOI、数值、标题；缺失标 `pending_human`/`not_reported`/`undetermined`。
- 冲突信息不隐藏：同一指标来源不一致时两条都记录，标 `source_conflict`，写入 `conflicts`。
- 翻译失败回退术语表查询，未验证译文标 `unverified_translation`。
- 网络失败时 `insufficient_evidence` 或 `partial`，写清已完成与未完成。
- 确定性：同一输入重复运行结果一致，排序使用稳定键（证据 id、DOI、标题），禁止随机性。
