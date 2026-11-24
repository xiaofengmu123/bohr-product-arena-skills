# 方法规则（references/method.md）

本文件是 `SKILL.md` 第 5 节的展开：给出跨语言检索每一步的判定规则，保证同一输入产生一致、可复核的结果。与代码无关，供 Agent 逐条执行、评审逐条核对。

## 1. 范围与语言路由
1. 先读 `/input/case.json` 与 `/input/task.md`，识别研究问题、目标语种、检索范围与任何 `known_item_hint`（已知条目线索：DOI、题名、作者、出版方）。
2. 源语言与目标语种要写清并解释依据，不得默认“只有英语”。语言标识用 ISO 639-1（pt、en、zh、de、ja 等）。
3. 路由规则：原文语言查询用原文术语表；目标语言查询用受控译文（记录服务与版本）；对“已知条目”优先按 DOI/题名精确定位，再按术语扩展召回。
4. 每个目标语种都要产生至少一条查询式；查询式按语种分别落 `queries`，不能只用母语拼接。

## 2. 术语表（terminology_table）
每条记录至少含：`term`、`language`、`type`、`basis`、`verification`、`variants`（可选）。
- `type ∈ {headword, synonym, abbreviation, hyponym, hypernym, transliteration, local_name}`；
- `basis`：该译法/变体出自哪份材料（材料路径或 DOI），并附逐字片段；
- `verification ∈ {exact, aligned, unverified_translation}`——`exact` 表示与原文用词一致，`aligned` 表示经来源确认对齐，`unverified_translation` 表示仅机器译文、未核对；
- 缩写要写全称映射；不同语种里同形缩写指向不同概念时，拆成两条并标注 `source_conflict`。
- 术语对齐必须可被第三方复核：任何无 `basis` 的术语不得当作已核对。

## 3. 逐语种查询（queries）
每条查询记录：`query`（原文）、`language`、`service`（翻译服务名）、`service_version`、`source_term`、`translation`（译文对照）、`status ∈ {run, skipped, failed}`。未记录 `service`/`version` 的译文不得当作已核对；翻译失败时回退纯术语表查询并标 `unverified_translation`。

## 4. 命中与版本模型
每条命中（`hits` 元素）至少含：`title`、`authors`、`year`、`venue`、`doi`/`url`、`language`（检索命中页面的语言）、`body_language`（正文语言）、`version`、`attribution`、`locator`、`quote`、`relevance`。

`version` 判定：
- `original`：正文为作者原语的原文版本；
- `translation`：出版方/期刊声明的全文译文（记录译者/出版方声明为 `attribution` 依据）；
- `abstract_only`：仅摘要可见，正文不可得；
- `duplicate`：同一论文同一语言/同一版本的重复记录；
- `undetermined`：语种或版本无法判定，写入 `uncertainty` 并说明。

判定依据必须写出来：正文语言、摘要语言、期刊“双语刊”声明、出版方翻译声明、DOI 指向的版本。正文不可得时不得标 `original` 或 `translation`。

## 5. 去重（dedup）
同一论文的多个语种版本或多条记录去重后只计一次：`dedup` 每条给 `canonical`（保留哪条）、`duplicates`（被合并的记录）、`basis`（按 DOI/题名/作者/出版方判定）。去重依据要可复核；`version` 不同的版本在 `dedup` 中并列保留，并在 `results` 里标注为同一作品的多语视图，不重复计数。

## 6. 证据对齐与误译复核
1. 每个关键结论/数值/命中都要 `locator`（URL、DOI、材料段落定位）+ 逐字 `quote`；缺失标 `pending_human`。
2. 高风险命中做回译或人工抽检，识别“词面对应、语义不同”的假阳性，判定写入 `evidence_map` 并标 `source_conflict`。
3. 冲突不隐藏：同一指标两个来源不一致时，两者都保留并说明。

## 7. 遗漏与状态
- 无结果 → 写入 `missing`（说明查询了什么、为什么无结果），不补造文献；
- 材料缺失关键数值 → `not_reported` 或 `pending_human`；
- 完成全部契约字段并自检通过 → `status=ok`；只完成部分 → `partial`（写明已完成/未完成）；材料不足 → `insufficient_evidence`；输入损坏 → `input_error`。

## 8. 运行时案例边界（来自题目，务必核对）
题目固定 3 个案例是**双向（葡萄牙语↔英语）的“已知条目”检索题**：给定研究问题，找回 3 篇真实工作、共 6 个视图。视图包括葡语原文正文、英语原文正文、出版方全文译文、英语仅摘要，以及一条显式精确重复。评分要点：保留 source/DOI/version/body_language/attribution，正确区分原文/译文/仅摘要/重复并去重。不涉及实时覆盖、未见语种或新研究声明，因此不要声称“发现新研究”或“覆盖全部语种”，只对被实际找回的条目说话。

据此，`version` 枚举（original/translation/abstract_only/duplicate/undetermined）与第 4、5 节必须严格执行；`attribution` 要记录出版方/译者/期刊的翻译声明；同一作品的葡语原文、英语原文、英语译文、英语摘要、重复记录在 `dedup` 中归为一组并标注各视图的 `version`，`results` 只按去重后的作品计数一次。

示例（示意，非正式案例答案）：某作品以葡语正文发表，后被同一出版方全文英译，另有仅英文摘要的索引页和一条完全重复的英文记录。此时应产出 1 条 canonical 作品记录 + 4 条视图标注（`original(pt)`、`translation(en, 出版方声明)`、`abstract_only(en)`、`duplicate(en)`），`dedup` 记录归并依据（DOI/题名/作者），`results` 计数为 1 个作品而非 5 条独立命中。
