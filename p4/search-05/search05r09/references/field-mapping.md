# 跨库字段映射与合并规则

本表是 `fedsearch.py` 的实现依据，也是他人复核/复用时的规则说明。目标是：**同一字段跨库语义对齐、缺失与未知区分、合并结果确定可重复**。

## 1. 库识别（结构指纹）

| 库 | 判定特征 | 记录定位 |
|---|---|---|
| Crossref | `message.items[]` | `message.items[i]` |
| EuropePMC | `resultList.result[]` | `resultList.result[i]` |
| PubMed esummary | `result` 为 uid→记录 映射，记录含 `title`/`articleids`/`authors` | `result.<uid>` |
| PubMed esearch | `esearchresult.idlist[]` | 仅 id，无完整记录，单独标注 |
| 出版社（异构） | `items`/`records`/`results`/`documents` 列表，或递归找到的文献型 list[dict] | `<key>[i]` 或 `<path>[i]` |
| 未知 | 以上皆不匹配 | 归为 `unknown`，记录不进任何库统计 |

## 2. 统一 schema 与字段来源

| 统一字段 | Crossref | EuropePMC | PubMed | 出版社/未知（别名） |
|---|---|---|---|---|
| title | `title[0]` | `title` | `title` | title/Title/articleTitle/name/display_name |
| authors | `author[]` given+family | `authorString` 或 `authorList.author[]` | `authors[].name` | authors/author/creators/byline |
| year | `issued.date-parts[0][0]` | `pubYear` | `pubdate` 首个 4 位年 | year/pubYear/publicationYear/published/date |
| journal | `container-title[0]` | `journalTitle` 或 `journalInfo.journal.title` | `fulljournalname`/`source` | journal/journalTitle/container-title/source/venue |
| volume | `volume` | `journalVolume` | `volume` | volume/journalVolume |
| issue | `issue` | `issue` | `issue` | issue/number |
| pages | `page` | `pageInfo` | `pages` | pages/pageInfo/page_range |
| doi | `DOI`（归一：小写、去前缀） | `doi` | `articleids[].value`(idtype=doi) | doi/DOI/prism:doi/pii |
| pmid | — | `pmid` | `uid`/articleids(pmid) | pmid/PMID |
| pmcid | — | `pmcid` | articleids(pmcid) | pmcid/PMCID |
| issn | `ISSN[0]` | `journalInfo.journal.issn` | `issn` | issn/ISSN/prism:issn |
| doc_type | `type` | `pubTypeList.pubType[0]`/`pubType` | `pubtype[0]` | type/pubType/docType/article_type |
| abstract | `abstract` | `abstractText` | —（esummary 通常无） | abstract/Abstract/description/summary |
| publisher | `publisher` | `publisher` | — | publisher/publisher_name |

规则：缺失→`null`（authors/doc_type 等集合→`[]`），绝不写占位串；`missing_metadata` 单独列明缺失字段。

## 3. 去重规则（union-find，按序执行，判定可解释）

1. **标识符精确**：DOI 归一后完全相等，或 PMID/PMCID 相等。
2. **标题+一作+年份**：归一标题（小写/去变音符/非字母数字转空格）+ 一作姓氏 + 年份完全相等。
3. **标题模糊**：同年且归一标题 `SequenceMatcher.ratio() >= 0.90`。

每个簇记录 `dedup.method` ∈ {doi_exact, identifier_exact, title_author_year, fuzzy_title, singleton}，保留 `member_indices` 供人工仲裁。

## 4. 合并与冲突消解

- 库优先级（高→低）：`crossref > europepmc > pubmed > publisher/pubmed_esearch > unknown`。
- 标量字段：取最高优先级来源的第一个非空值。
- `title`/`abstract`：取最长非空值；`authors`：取最长列表。
- `doi`/`pmid`/`pmcid`/`year` 出现多个不同非空值 → 记入 `conflicts`（含各来源与取值），仍按优先级消解并写明 resolution。
- 每个字段保留 `field_sources`（db + source_file + locator），每个候选保留 `raw_snapshot`（逐字字段）。

## 5. 覆盖比较

`coverage.per_database.<db>` 列出 `total_records`、`fields_missing`（该库所有记录均缺失的字段）、`field_coverage`（逐字段非空计数）；`records_in_single_db_only` 列出只被单一库收录的合并记录；据此说明覆盖盲区。
