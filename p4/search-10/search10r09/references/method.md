# 方法细则（references/method.md）

本文是 `SKILL.md` 的补充细则；与 SKILL 保持一致，不重复其正文。

## 1. 固定案例的边界
三个案例为**双向跨语言已知条目对位**：3 部作品 / 6 个视图，原始正文为葡萄牙语（PT）或英语（EN），并包含：
- 出版方全文翻译（`full_translation`）；
- 仅英文摘要视图（`abstract_only`：正文一种语言、摘要另一种语言）；
- 显式精确重复（`duplicate`：同一版本重复计入）。

必须逐条保留 `source`、`doi`、`version`、`body_language`、`attribution`（署名/出版方），不得依据错误的领域标签补造数据。

## 2. 已知条目对位流程（核心方法）
1. **读题**：从 case.json 提取被问的条目（标题/作者/DOI 片段）、源语言与目标语种；无法确定源语言时标 `undetermined`。
2. **术语对齐**：为标题中的核心术语建表，逐条记录 `type`（synonym/abbreviation/hyponym/transliteration/local_name）、`basis`（来源）与 `verification`（验证方式）。
3. **逐语种查询**：PT 与 EN 各构造查询，记录翻译服务与版本、原文-译文对照；未记录来源的译文标 `unverified_translation`。
4. **取回与对齐**：每条命中写 `locator`（URL/DOI/材料段落）与逐字 `quote`，并判定 `language` 与 `version`。
5. **去重**：用 `scripts/main.py norm` 归一 DOI/标题；同一 DOI 或「归一标题+首作者姓+年份」一致视为同一作品，跨语种归并到 `works[]`，精确重复标 `duplicate`。
6. **复核**：对高风险命中做回译或人工抽检，识别"词面对应、语义不同"的假阳性，写入 `evidence_map`/`conflicts`。

## 3. 字段填法速查
- `results[]`：`title`、`authors[]`、`year`、`venue`、`doi`、`url`、`language`、`version`、`body_language`、`abstract_language`、`source`、`locator`、`quote`、`relevance_basis`。
- `works[]`：`work_id`、`canonical_title`、`canonical_authors[]`、`canonical_year`、`canonical_doi`、`version_ids[]`、`dedup_basis`。
- `dedup[]`：`pair`（两个命中 id）、`basis`（DOI 或标题归一一致）、`decision`（merge/keep_separate）、`note`。
- `evidence_map[]`：`claim`、`locator`、`quote`、`result_id`。
- `terminology_table[]`：`source_term`、`language`、`variants[]`、`type`、`basis`、`verification`。

## 4. 可复核性规则
- 每条结论/数值必须能指回材料的逐字片段（locator + quote）；冲突时两个来源都记并标 `source_conflict`。
- 缺失元数据写 `not_reported`，无法判断写 `pending_human`，绝不补造 DOI、引用或数值。
- 语言判定用 `scripts/main.py lang` 初判后必须复核：正文语言、摘要语言、出版方翻译声明三者一致才定 `version`。

## 5. 自检（对应评分维度）
- [ ] 接口与触发：frontmatter 齐备，`--help` 退出码 0，用户能走完一次完整检索；
- [ ] 运行与产物：空输入/坏 JSON 不崩溃且退出码 0，骨架 3 分钟内落盘，原子写无半截 JSON，重复运行一致，`validate` 通过；
- [ ] 检索质量：research_question 明确，术语表逐条可核，逐语种查询有对照，命中带 locator+quote，多语版本已去重；
- [ ] 诚实与复核：无编造，异常处置有记录，status/uncertainty 与证据强度一致；
- [ ] 交付：SKILL.md ≤180 行，文件 ≤14、总体积 ≤1.2MB，脚本仅标准库，无预写答案。

## 6. 限制与维护
- 语言识别为启发式，仅对 PT/EN 拉丁文本可靠；其他语种需人工确认或标 `undetermined`。
- 术语表、查询模板、去重规则可被他人复用；更新词表时保持 `type/basis/verification` 三要素。
- 不声称覆盖全部语种或"未见语言"；只处理案例中真实出现的原文、译文与摘要视图。
