# 跨库字段映射、去重与合并规则

本文件是 `scripts/core_impl.py` 的规则说明，可被他人复用于其他多库检索流程。字段映射只处理
`/input` 下真实存在的响应，不联网、不补造。

## 1. 响应类型识别（先发现，不假设文件名）

| 特征结构 | 识别为 db | 记录定位 |
|---|---|---|
| `message.items[]` | `crossref` | `message.items[i]` |
| `resultList.result[]` | `europepmc` | `resultList.result[i]` |
| `esearchresult` 或 `result{uids[]}` | `pubmed-esearch` | 无文献记录（仅 id 列表） |
| `result` 下 `uid -> dict` 且含 `uid/title` | `pubmed-esummary` | `result.<uid>` |
| 顶层 `items/results/records/data/publications/documents/hits[]` | `publisher` | 对应 `key[i]` |
| 顶层数组 | `publisher` | `[i]` |
| 其他 | `generic`（仍尽力抽取） | — |

`esearch` 只含 PMID 列表，不产出候选；脚本在 `sources` 中标注 `record_count: 0` 并写入
`empty_results`，不把 id 列表冒充文献。

## 2. 字段归一映射（明确缺失 vs 未知）

归一后统一字段：`title, authors, year, journal, volume, issue, pages, doi, pmid, pmcid,
issn, doc_type, abstract, publisher, identifiers`。

| 统一字段 | Crossref | EuropePMC | PubMed (esummary) | Publisher（尽力） |
|---|---|---|---|---|
| title | `title[0]` | `title` | `title` | `title` |
| authors | `author[].family/given` | `authorString` 或 `authorList.author[]` | `authors[].name` | `authors/author/creator` |
| year | `published-print/online/issued.created.date-parts[0][0]` | `pubYear` / `firstPublicationDate` | `pubdate/sortpubdate/epubdate` | `year/publicationYear/date` |
| journal | `container-title[0]` | `journalInfo.journal.title` / `journalTitle` | `fulljournalname/source` | `journal/journalTitle` |
| volume/issue | `volume`/`issue` | `journalInfo.volume`/`issue` | `volume`/`issue` | 同名 |
| pages | `page` | `pageInfo` | `pages` | `pages/page/pageInfo` |
| doi | `DOI`（去 `https://doi.org/` 前缀） | `doi` | `articleids[].value`（idtype=doi） | `doi/DOI` |
| pmid | — | `pmid` | `uid`/`articleids`(idtype=pubmed) | `pmid/PMID` |
| pmcid | — | `pmcid` | `articleids`(idtype=pmc) | `pmcid/PMCID` |
| issn | `ISSN[0]` | `issn` | `issn` | `issn/ISSN` |
| doc_type | `type` | `pubType` | `pubtype[0]` | `type/docType` |
| abstract | `abstract`（去 JATS 标签） | `abstractText` | —（esummary 无摘要） | `abstract/description` |
| publisher | `publisher` | `journalInfo.journal.publisher` | — | `publisher` |

缺失值一律填 `null` 或 `[]`，与"未知/未报告"不混用；`systematically_missing_fields` 列出
每个库在全部记录中都缺失的字段（如 PubMed esummary 系统性缺 `abstract`）。

## 3. 去重判定（可解释，保留仲裁入口）

按确定性顺序分四轮，每条记录只进入一个簇：

1. **DOI 精确**：归一 DOI（小写、去前缀）相等。
2. **标题 + 一作 + 年份**：归一标题、首位作者小写、年份三者相等。
3. **标题 + 年份**：归一标题、年份相等（用于缺作者的记录）。
4. **标题模糊**：归一标题 `SequenceMatcher` 相似度 ≥ 0.90 且年份相同。

归一标题：转小写、去 HTML、去标点、多空格折叠。判定链写入每条候选的
`provenance.merge_rule`（`doi_exact` / `title_author_year` / `title_fuzzy` / `singleton`），
不可自动裁决的模糊边界列入 `pending_human`，不自动拆分。

## 4. 字段合并优先级与冲突消解

合并时字段按"库优先级 + 字段优先级"取首个非空值，保证结果稳定、可重复：

- 库优先级：`crossref > europepmc > pubmed > publisher > generic`。
- 字段覆盖：`doi` 优先 Crossref；`pmid` 优先 PubMed；`pmcid` 优先 EuropePMC；`abstract` 优先
  EuropePMC（Crossref 常为 JATS 标签）。

标量字段（`doi/year/title/journal/volume/issue/pages/pmid/pmcid/issn/doc_type/publisher`）在多个
来源取值不同时，记录进 `conflicts[]`（含各方值与来源），并标注 `resolution: priority_order`；
人工可通过 `decisions.json` 覆盖。

## 5. 来源回溯与证据

每条归一记录保留：`_db`（来源库）、`_source_file`（原始响应文件相对路径）、`_locator`
（响应内定位，如 `message.items[3]`）、`_request`（响应中的查询上下文，尽力提取）、
`_raw`（原始记录对象）。合并后的候选在 `provenance.raw_snapshots[]` 提供 `_raw` 的逐字
JSON 片段（≤2000 字符，超出截断并说明）；`evidence_records[]` 为每条来源记录单独提供
`locator + quote`，实现"库—查询—原始响应—映射—去重—合并"的逐条复核链路。
