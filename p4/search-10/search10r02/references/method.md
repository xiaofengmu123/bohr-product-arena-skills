# 方法规则（references/method.md）

本文是 `SKILL.md` 的方法细则，供执行时逐条对齐；与 SKILL.md 冲突时以 SKILL.md 为准。

## 1. 已知项（known-item）检索原则
本任务多为"已知项"：case 给出一篇真实论文的部分标识，要求找回其原文与另一语种版本，并说清二者关系。目标是**坐实已知项，再补版本**，而非开放式海量召回。

- 先抽取种子标识：DOI、arXiv/PMID、作者、年份、标题片段、语种声明。
- 命中分两类：`known_item_match`（就是 case 指的那篇）与 `related`（同主题但不同论文）。排序时前者优先。
- 不能把"同主题文献"冒充"已知项"；相关性依据必须落到逐字片段。

## 2. 语言对与方向
- 语种归一化表见 `scripts/main.py` 的 `LANG_ALIASES`（如 `pt/por/português`→`pt`，`en/eng`→`en`）。
- 每个案例确定一个双向对（典型为 `pt↔en`）与检索方向：源语言→目标语种。
- 语种无法确定时标 `undetermined` 并写入 `uncertainty`，不得猜测后当作事实。

## 3. 术语对齐（terminology_table）
- 每条术语记录：源词、语种、变体（同义词/缩写/上下位/音译/本地译名）、type、basis（依据，如"出版方译名/学科词典/原文标题"）、verification（验证方式，如"回译/命中标题逐字对齐"）。
- 学科用词优先于字面直译；缩写在不同语种可能指向不同概念，必须写清消歧依据。
- 未经验证的译文标 `unverified_translation`，不得当作已核对。

## 4. 逐语种查询（queries）
- 每种目标语种至少一条查询式，记录翻译服务与版本、原文-译文对照。
- 检索用 web_search / paper_search，逐语种分别构造，不得只用母语。
- 查询与论文原文用词不一致会导致召回骤降，需据术语表扩展同义词、词形变化、复合词、语序与中英混排标题。

## 5. 版本与语言判定（results / dedup）
每条命中必须标注：
- `body_language`：正文语言（据正文/摘要/出版方声明判定，写依据）；
- `version_type`：`original` / `publisher_translation` / `abstract_only` / `bilingual` / `undetermined`；
- `locator` + `quote`：来源定位与逐字证据。

去重以"论文作品"为单位：DOI 归一化 + 标题/作者指纹合并同一论文的多语版本，去重依据写入 `dedup`。声明语种都应有有效结果；同一论文多语版本只计一次。

## 6. 误译复核（evidence_map / conflicts）
- 对高风险命中做回译或人工抽检，识别"词面对应、语义不同"的假阳性，记录判定依据。
- 冲突信息不隐藏：同一指标来源不一致时两个都记录并标 `source_conflict`。

## 7. 证据强度与诚实
- 结论动词与证据强度一致：已坐实用"表明/证实"，初步证据用"初步表明"。
- 不虚构 DOI、引用、数值；缺失标 `pending_human` 或 `not_reported`。
- 每步 patch 后运行 `scripts/main.py validate`；收尾前确认 `status` 与 `uncertainty` 与证据一致。
