# 方法规则（references/method.md）

本文件是 SKILL.md 的补充实现规范。执行时按第 4 节流程走，本文件说明每个产物的字段含义、判定规则与证据要求。

## 1. 两种运行模式（读题后立即判定）

- `topic_discovery`：研究问题是开放式的，目标是从零召回相关文献。此时先建立术语表，再逐语种扩展查询。
- `known_item`：题目给定一篇已知文献的题录/DOI/片段线索，目标是核验该文献、找回其在各语种的版本并去重。此时优先构造"题录指纹"查询（作者 + 年份 + 标题特征词，必要时加 DOI），而不是宽泛主题查询；所有命中必须与给定线索做"同一论文"判定（标题逐字/归一化、DOI、作者-年份组合）。
- 模式判据写入 `research_question.mode`；无法判定写 `undetermined` 并说明。

## 2. 研究问题（research_question）

字段：`question`（澄清后问题）、`mode`、`source_language`（提问/线索语种）、`target_languages`（需要检索的语种列表）、`scope`（时间、文献类型、来源边界）。目标语种从题目约束与材料线索推导，不凭空多报语种；每声明一个目标语种，步骤 3 必须有对应的查询式，步骤 4 必须有对应结果或明确"该语种无结果"。

## 3. 术语表（terminology_table）

每条术语记录：`term`（源词）、`language`、`variants`（同义词、缩写、上下位、音译、本地译名）、`type`（synonym/abbreviation/hypernym/transliteration/local_name/compound）、`basis`（依据：材料出处、词典、论文原文用词）、`verification`（验证方式：回译一致、原文出现、人工确认、unverified）。术语表驱动查询生成：每个目标语种的查询词优先取该语种已验证变体，未验证变体必须标 `unverified_translation` 并在查询式里注明。

## 4. 逐语种查询（queries）

每条查询记录：`query_text`、`language`、`translation_service`（如未用翻译则写 `none`/`terminology_only`）、`service_version`、`source_terms`（源术语）、`translated_terms`（对应译名）、`note`（构造方式：题录指纹/术语组合/整句机翻）。翻译与查询的原文-译文对照必须成对保留；未记录服务与版本的译文视为未核对，不得作为术语依据。

## 5. 语言与版本判定（results）

每条命中的语言与版本分开记录：
- 语言依据：`body_language`（正文实际语种，来自实际解析文本）、`title_language`、`abstract_language`、`publisher_statement`（双语刊声明/翻译声明）。依据写入 `language_evidence`（locator + quote），不能只写一个结论词。
- 版本取值：`original`（原文）、`translation`（全文译文，注明出版方翻译）、`bilingual`（双语刊）、`abstract_only`（仅摘要）、`undetermined`（无法判定）。原文不可得时不冒充全文。
- 来源字段：`title`、`authors`、`year`、`venue`、`doi`/`url`、`language`、`version`、`relevance_basis`（相关性依据，逐字引用）、`locator`（URL/DOI/材料段落定位）。

## 6. 去重（dedup）

同一论文的多语版本以"规范键"归并，键优先级：DOI → 标题归一化（小写、去标点、去连字符）→ 作者首作者 + 年份 + 标题特征词。每组记录：`canonical_id`、`versions`（各语种版本及依据）、`key_used`（用了哪个键）、`confidence`。去重后 `results` 中每个规范作品只计一次；被归并版本作为该条目的 `versions` 子列表，不重复计入计数。判定依据写入 `dedup.basis`。

## 7. 证据对齐（evidence_map）

每条关键结论/数值/命中必须有 `locator`（材料段落、URL、DOI）+ 逐字 `quote`。冲突不隐藏：同一指标来源不一致时两条都记录并标 `source_conflict`。缺失标 `pending_human` 或 `not_reported`，不补造。翻译对不上、回译语义不一致的命中标 `translation_mismatch` 并归入 `conflicts`。

## 8. 误译复核（高风险命中）

对"词面对应、语义不同"的高风险命中做回译或人工抽检：把命中标题/摘要回译到源语言，与源问题/源术语比对，判断是否同义。结果写 `backtranslation`（回译文本）、`semantic_match`（true/false/uncertain）、`basis`。不匹配者降权或移出，判定依据保留。

## 9. 评分维度 → 产物映射（落地检查）

| 评分点 | 产物字段 | 完成标准 |
|---|---|---|
| 任务完成度 15 | research_question / terminology_table / queries / results / evidence_map | 五类字段非空且自洽 |
| 结果正确性 15 | results[].language / version / terminology[].term / evidence_map | 语种与版本有依据；术语与原文一致；译文同义经回译 |
| 结果完整性 5 | results / dedup / missing | 声明语种均有结果；去重后计数；遗漏明确 |
| 结果可核验性 5 | queries[].translation_service / results[].locator / evidence_map[].quote | 他人可逐条复核 |
| 使用增益 25 | change_log / dedup / queries | 增量记录、去重计数、复用术语表 |
| 创新增益 15 | 语言识别与路由、版本归并、回译复核 | method.md 本文件说明边界与价值 |
| 可靠体验 10 | SKILL.md 第 7 节 + 脚本 | 原子写、增量 patch、降级策略 |
| 生态贡献 10 | 本文件 + checklist.md + 模板字段 | 可复用规则与模板 |

## 10. 预算与确定性

总预算 900 秒、64 次模型请求。步骤 0 在 180 秒内落盘骨架，之后每次 patch 只增量合并，不重写整份结果。所有列表按确定性顺序（发现顺序或字母序）生成，禁止随机采样与时间依赖排序；`runtime` 时间戳仅作运行元数据，不作为结果内容比较依据。
