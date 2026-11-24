# 字段映射、去重与合并规则

本文档是可复用规则说明，`scripts/core_impl.py` 依此实现。他人可按本表核对脚本行为或移植到其他语言。

## 1. 跨库字段映射

规范字段：`title / authors / year / journal / volume / issue / pages / doi / pmid / pmcid / issn / doc_type / abstract / publisher / identifiers`。

| 规范字段 | Crossref (`message.items[]`) | EuropePMC (`resultList.result[]`) | PubMed (esummary `result[uid]`) | 出版社/聚合器（通用） |
|---|---|---|---|---|
| title | `title[0]` | `title` | `title` | `title` / `articleTitle` / `dc:title` |
| authors | `author[]`（given+family 合并为 "Family, Given"） | `authorList[]`，缺失则 `authorString` | `authors[].name` | `authors` / `author` / `creators` |
| year | `issued.date-parts[0][0]` | `pubYear` | `pubdate`（正则取 4 位年） | `year` / `publicationYear` |
| journal | `container-title[0]` | `journalTitle` 或 `journalInfo.journal.title` | `source` / `fulljournalname` | `journal` / `publicationTitle` |
| volume | `volume` | `journalVolume` | `volume` | `volume` |
| issue | `issue` | `issue` | `issue` | `issue` |
| pages | `page`，缺失则 `article-number` | `pageInfo` | `pages` | `pages` / `page` |
| doi | `DOI`（去 `https://doi.org/` 前缀、lowercase） | `doi` | `articleids[].value`（idtype=doi），或 `elocationid` 的 `doi:` 前缀 | `doi` |
| pmid | — | `pmid` | `uid` | `pmid` |
| pmcid | — | `pmcid` | — | `pmcid` |
| issn | `ISSN[]` / `issn-type[].value` | `journalInfo.journal.issn` | — | `issn` |
| doc_type | `type` | `pubType` | `pubtype[0]` | `doc_type` / `type` |
| abstract | `abstract`（若存在） | `abstractText` | `abstract` | `abstract` / `description` |
| publisher | `publisher` | `publisher` | — | `publisher` |

归一化要点：

- DOI 统一去前缀并 lowercase，但**不伪造**：不同来源原始写法在 evidence 中仍保留逐字 `quote`。
- 年份只接受 `1400–2200` 区间整数；无法解析记 `null` 并进入 `missing_metadata`。
- ISSN 取 `value` 后去重、排序；列表字段（authors/issn）合并取并集。
- 缺失（字段不存在）与未知（存在但无法解析）都记 `null`，不写 `"N/A"` 之类占位符，避免与真实值混淆。

## 2. 去重判定（优先级从高到低）

| 规则 | 条件 | 判定 id |
|---|---|---|
| DOI 精确 | 归一化 doi 相同 | `doi_exact` |
| 标题+一作+年份 | 标题归一 + 第一作者归一 + 年份全等 | `title_author_year` |
| 标题模糊 | 标题归一后 `SequenceMatcher.ratio >= 0.90` 且同年 | `title_fuzzy_0.90` |

标题归一：转小写后仅保留 `[0-9A-Za-z\u4e00-\u9fff]`。作者归一同。所有去重边写入 `evidence_map.dedup_clusters`，每条边带 `quote_left` / `quote_right` 与两侧 `locator`，判定可解释、可复核。

## 3. 合并取值优先级

默认按库优先级：`crossref > europepmc > pubmed > publisher > generic > unknown`。个别字段按语义覆盖默认：

| 字段 | 覆盖优先级 |
|---|---|
| doi | europepmc = crossref > pubmed > publisher |
| abstract | europepmc = pubmed > crossref > publisher |
| authors | crossref（结构化）> europepmc > pubmed |
| year | crossref > europepmc = pubmed |

冲突消解：

- 同一去重簇内同一标量字段出现 ≥2 个不同取值时，选优先级最高者，同时写入 `evidence_map.conflicts`（含各来源取值 + locator + quote）。
- `year / title / doi / pages` 的冲突标记 `needs_human_arbitration = true`；其余字段自动消解、不阻塞交付。
- 列表字段（authors/issn）取并集、不视为冲突。
- 消解结果稳定：排序键由 `(字段优先级, 成员 db, 原序号)` 唯一确定。

## 4. 来源回溯链

每条候选的 `provenance[]` 记录：`db — source_file — locator（文件#JSON指针）— record_index — query — raw_snapshot`。`field_sources` 记录每个字段取自哪个成员与哪条证据。完整链：库 → 查询 → 原始响应 → 映射 → 去重 → 合并。

## 5. 缺失与未知

- 某库整库缺失 → 不进 `sources`/`coverage.databases`，写 `limitations`。
- 某字段缺失 → 填 `null`，记 `missing_metadata` 与 `coverage.per_db_fields_missing`。
- 无法判断（响应空、无 bibliographic 记录）→ `empty_results` + `note`，不编造。
