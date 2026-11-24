# 跨库字段映射与合并规则

本文档是 `scripts/core_impl.py` 的实现规范，供人/Agent 复核归一化、去重与合并是否按声明执行。代码内置相同规则，`references/` 仅供查阅，不参与运行时计算。

## 1. 库识别

| 响应特征 | 库 | 记录定位（locator） |
|-|-|-|
| `message.items[]` | Crossref | `message.items[i]` |
| `resultList.result[]` | EuropePMC | `resultList.result[i]` |
| `result.uids[]` + `result[uid]` | PubMed（esummary） | `result[uid]` |
| `esearchresult.idlist[]` | PubMed（esearch，仅 ID） | `esearchresult.idlist[i]` |
| 含 title + 标识符/作者的异构 JSON | publisher / generic | 递归 JSON 路径 |

无记录特征的 JSON 记为 `record_count=0` 的配置/空响应，不编造记录。

## 2. 规范字段与跨库映射

| 规范字段 | Crossref | EuropePMC | PubMed | generic/publisher |
|-|-|-|-|-|
| title | `title[0]` | `title` | `title` | `title` |
| authors | `author[].given+family` | `authors.author[].fullName` 或 `authorString` | `authors[].name` | `authors/author` |
| year | `published-print/online/published/issued/created.date-parts[0][0]` | `pubYear` | `pubdate/epubdate` 取 4 位年 | `year/pubYear/date` |
| journal | `container-title[0]` | `journalTitle` 或 `journalInfo.journal.title` | `fulljournalname` 或 `source` | `journal/journal-title/source` |
| volume | `volume` | `journalInfo.volume` | `volume` | `volume` |
| issue | `issue` | `journalInfo.issue` | `issue` | `issue/number` |
| pages | `page` | `pageInfo` | `pages` | `pages/page/pageInfo` |
| doi | `DOI`（小写去前缀） | `doi` | `articleids[doi]` | `doi` |
| pmid | `PMID` | `pmid` | `uid` | `pmid` |
| pmcid | `PMCID` | `pmcid/pmcId` | `articleids[pmc]` | `pmcid` |
| issn | `ISSN`/`issn-type[]` | `journalInfo.issn` | `issn` | `issn` |
| doc_type | `type` | `pubType/source` | `pubtype[]` | `type/pubType` |
| abstract | `abstract`（去 XML） | `abstractText`（去 XML） | 通常无 | `abstract` |
| publisher | `publisher` | `publisher/journalInfo.publisher` | 通常无 | `publisher` |

缺失与未知严格区分：源里没有对应键 → `null`；源里有键但值为占位（`-`、`N/A`、空）→ 也按 `null` 处理并计入 `missing_metadata`，不写成 `not_reported` 冒充存在。

## 3. 去重优先级（确定性）

1. **DOI 精确**：归一化 DOI（小写、去 `https://doi.org/` 与 `doi:` 前缀）相同 → 同簇。
2. **标题 + 一作姓氏 + 年份**：归一化标题键、一作姓氏键、4 位年份三者精确相同 → 同簇。
3. **标题模糊（同年）**：仅当同年，且标题相似度 ≥ 0.90（`difflib.SequenceMatcher` 或去停用词后 Jaccard 取大者）→ 同簇。

同簇判定通过并查集（union-find）传递闭包；簇按最小原始下标确定性排序；每簇记录 `dedup_basis`（doi_exact / title_first_author_year_exact / title_fuzzy_same_year / singleton）。

## 4. 合并与冲突消解

- 每个规范字段从本簇“提供该字段”的来源中按**字段优先来源**取非空值：
  - 全局库优先级：Crossref > EuropePMC > PubMed > publisher > generic。
  - 标识符专用：`doi` 优先 Crossref；`pmid` 优先 PubMed；`pmcid` 优先 EuropePMC；`abstract` 优先 EuropePMC。
- `field_sources[field]` 记录本簇所有提供该字段的 `{db, source_file, locator}`，不丢弃次要来源。
- 冲突：同一字段不同库给出**不同非空归一值** → 写 `conflicts[]`，含所有取值与来源，resolution=`priority_source`，并保留被选值与来源库。
- 列表字段（authors/issn）取优先级最高来源的完整列表，其它来源写入 `field_sources`，不做跨库拼接以免引入错误合并。

## 5. 来源回溯（库—查询—原始响应—映射—去重—合并）

每条合并记录的 `provenance[]` 含每个来源库的：

```json
{
  "db": "crossref",
  "source_file": "materials/crossref-1.json",
  "locator": "message.items[3]",
  "query": "搜索式或 null",
  "raw_snapshot": { "title": "...", "DOI": "...", "author": [...] }
}
```

`raw_snapshot` 是原始响应里的**逐字片段**，不是改写/转述；`locator` 直接定位到源文件内记录位置。

## 6. 覆盖与缺口

- `coverage.databases`：实际解析到记录的库。
- `coverage.total_input_records` / `merged_record_count` / `duplicates_merged`：输入记录数、合并后记录数、去重掉的数量。
- `coverage.records_in_single_db_only`：仅被一个库收录的合并记录数（覆盖差异的直接证据）。
- `coverage.systematically_missing_fields[db].fields_missing`：某库全部记录都缺失的规范字段（系统性缺口，而非个别缺失）。

未覆盖的库不进 `sources`/`coverage`，改在 `limitations`/`degradations` 里说明。
