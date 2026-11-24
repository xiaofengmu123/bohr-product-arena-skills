# 跨库字段映射与合并规则

本文档是 `scripts/fedsearch.py` 中字段归一、去重、合并规则的说明，供复核与复用。

## 1. 库类型识别（按结构，不按文件名）

| 库 | 识别特征 | 记录位置（locator 前缀） |
|---|---|---|
| Crossref | `message.items` 或 `message.total-results` | `message.items[i]` |
| EuropePMC | `resultList.result` 为列表 | `resultList.result[i]` |
| PubMed esummary | `result.uids` 为列表 | `result.<uid>` |
| PubMed esearch | `esearchresult`（仅 idlist，无题录） | 不产生候选，记入 empty_results |
| 出版社/其他 | `records`/`items`/`results`/`papers`/`articles`/`hits`/`docs` 列表 | `<key>[i]` |

locator 指向原始 JSON 中的真实位置，与 `quote` 中的逐字片段配合即可逐条复核。

## 2. 字段映射

统一字段：`title authors year journal volume issue pages doi pmid pmcid issn doc_type abstract publisher`。

| 统一字段 | Crossref | EuropePMC | PubMed esummary |
|---|---|---|---|
| title | `title[0]` | `title` | `title` |
| authors | `author[].{family,given}` | `authorList.author[].{fullName,given,family}` | `authors[].name` |
| year | `issued.date-parts` | `pubYear` | `pubdate` |
| journal | `container-title[0]` | `journalInfo.journal.title` | `fulljournalname`/`source` |
| volume/issue/pages | `volume`/`issue`/`page` | `journalInfo.volume/issue`/`pageInfo` | `volume`/`issue`/`pages` |
| doi | `DOI` | `doi` | `elocationid`(doi:) 或 `articleids[doi]` |
| pmid | — | `pmid`/`id` | `uid` |
| pmcid | — | `pmcid` | `articleids[pmc]` |
| issn | `ISSN`/`issn-type` | `journalInfo.journal.issn` | `issn` |
| doc_type | `type` | `pubType` | `pubtype[0]` |
| abstract | `abstract` | `abstractText` | 无（记缺失） |
| publisher | `publisher` | `bookOrReportDetails.publisher` | `source` |

缺失与未知严格区分：字段不存在填 `null` 并记入 `missing_metadata`/`fields_missing`；存在但语义不明才允许 `not_reported`。

## 3. 去重规则（确定性、可解释）

优先级由高到低：

1. 标识符精确匹配：`doi`（规范化后）→ `pmid` → `pmcid`。
2. 标题模糊匹配：标题归一化后 `SequenceMatcher.ratio() >= 0.90`，且（任一方年份缺失或年份相同），且（任一方一作姓氏缺失或一致）。

同一条记录只要与某个已有簇的任一成员匹配即并入该簇；簇内按库优先级排序以保证合并稳定。

## 4. 合并与冲突消解

每个字段按 `FIELD_PRIORITY` 中的库优先级取第一个非空值；不同库给出不同归一化值时记入 `conflicts`，保留 `chosen` 库与全部候选值供人工仲裁。

关键字段优先级（靠前者优先）：

- `doi` / `issn`：crossref > europepmc > pubmed > publisher
- `title`：crossref > pubmed > europepmc > publisher
- `journal` / `year`：pubmed > europepmc > crossref > publisher
- `abstract`：europepmc > pubmed > crossref > publisher

合并结果稳定可重复：同一输入、同一优先级规则必然得到同一份合并记录。
