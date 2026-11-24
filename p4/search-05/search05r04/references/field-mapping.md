# 字段映射、合并优先级与不确定性分层

本文件是 `scripts/core_impl.py` 中字段归一与合并规则的人读说明；脚本是唯一事实来源，二者需保持一致。

## 1. 跨库字段映射

| 统一字段 | Crossref (`message.items[]`) | EuropePMC (`resultList.result[]`) | PubMed esummary (`result[]`) | generic |
|-|-|-|-|-|
| `title` | `title[0]` | `title` | `title` | `title` |
| `authors` | `author[].{family,given}` | `authorList.author[].{lastName,firstName}` → `authorString` | `authors[].name` | `authors[]` |
| `year` | `published-print/online/issued.date-parts[0][0]` | `pubYear` | `pubdate` 正则 `(19|20)\d{2}` | `year/pubyear` |
| `journal` | `container-title[0]` | `journalTitle` | `fulljournalname/source` | `journal/venue` |
| `volume` | `volume` | `journalVolume` | `volume` | `volume` |
| `issue` | `issue` | `issue` | `issue` | `issue/number` |
| `pages` | `page` | `pageInfo` | `pages/elocationid` | `pages/page` |
| `doi` | `DOI` | `doi` | `articleids[doi]` | `doi` |
| `pmid` | — | `pmid` | `articleids[pubmed]`/`uid` | `pmid` |
| `pmcid` | — | `pmcid` | `articleids[pmc]` | `pmcid` |
| `issn` | `ISSN[]` | `journalIssn` | `issn` | `issn` |
| `doc_type` | `type` | `pubType` | `pubtype[0]` | `type` |
| `abstract` | `abstract` | `abstractText` | —（通常无） | `abstract` |
| `publisher` | `publisher` | `publisher` | — | `publisher` |

缺失与未知必须区分：源响应里没有该键 → `null` + `missing_metadata`；源响应里有键但值为空 → 仍按缺失记录，但原始片段保留。

## 2. 取值优先级（tier 越小越可信）

- 身份标识符：`doi` 以 Crossref 为准；`pmid` 以 PubMed 为准；`pmcid` 以 EuropePMC 为准。
- 书目字段（title/journal/year/volume/issue/pages/issn/publisher）与 `authors`：Crossref > EuropePMC > PubMed > generic。
- `abstract`：EuropePMC > PubMed > Crossref（Crossref 少有摘要）。
- `doc_type`：EuropePMC > PubMed > Crossref。

同 tier 多源给出不同值时，采用稳定顺序（`source_file` + `locator` 字典序）消解，并写入 `conflicts`，不静默覆盖。

## 3. 去重判定（可解释，逐级降级）

1. 标识符精确：DOI（大小写归一）或 PMID 或 PMCID 一致 → 同簇。
2. 标题 + 年份 一致 → 同簇。
3. 标题 + 一作姓氏 + 年份 一致 → 同簇。
4. 标题归一化相似度 ≥ 0.90 且同年 → 同簇（标 `identity_confidence=low`）。
5. 剩余单条成簇。

`provenance.matched_by` 记录实际使用的匹配级别，`identity_confidence` 给出 high/medium/low，供人工仲裁。

## 4. 字段级置信度分层（本版核心设计）

| 层级 | 含义 | 判定 |
|-|-|-|
| `exact` | 原始片段与输出值逐字一致 | `quote == value` |
| `normalized` | 经去空白/大小写/日期抽取等归一 | quote 与 value 经规则仍能对应 |
| `ambiguous` | 多来源值不同但同优先级 | 值集合 > 1 且无同 tier 冲突 |
| `conflicting` | 同 tier 来源给出不兼容值 | 同 tier 值不同 |
| `missing` | 所有来源均无该字段 | 无候选值 |

每个字段保留 `field_sources`（db / source_file / locator / 逐字 quote / priority），因此任何不确定项都可回溯到原始响应片段。

## 5. 覆盖比较口径

- `fields_missing_per_database`：某库贡献的字段数少于其输入记录数即计入缺失（显式列出系统盲区）。
- `records_in_single_db_only`：只出现在一个库的记录（跨库覆盖盲区）。
- `systematically_missing_fields`：所有库都缺失的字段（例如摘要普遍缺失）。
