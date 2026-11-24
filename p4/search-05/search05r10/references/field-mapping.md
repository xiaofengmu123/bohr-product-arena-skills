# 跨库字段映射与合并规则

本表定义异构数据库响应到统一记录模型的映射关系。实际映射在 `scripts/core_impl.py`
的 `normalize_record()` 中实现；此处为可复用、可人工核对的规则说明。

## 1. 库结构指纹（先发现，后假设）

| 库 | 判定特征 | 记录抽取路径 | locator 形式 |
|---|---|---|---|
| Crossref | `message.items[]` | `message.items[i]` | `message.items[3]` |
| EuropePMC | `resultList.result[]` | `resultList.result[i]` | `resultList.result[0]` |
| PubMed esummary | `result` 为 `{uid: record}` | `result.<uid>` | `result.12345678` |
| 出版社/通用 | `records[]` / `items[]` / `data[]` / `results[]` / `publications[]` / `documents[]` | 对应数组元素 | `<key>[i]` |
| 顶层数组 | JSON 顶层为对象数组 | `[i]` | `[0]` |

不依赖固定文件名；扫描 `/input/` 全部 `*.json` 后按上述结构识别。配置类 JSON
（无文献记录的字典）记为 `record_count: 0`，不误当文献。

## 2. 字段归一映射

统一记录模型字段：`title, authors, year, journal, volume, issue, pages, doi,
pmid, pmcid, issn, doc_type, abstract, publisher, identifiers`。

| 统一字段 | Crossref | EuropePMC | PubMed esummary | 通用出版社 |
|---|---|---|---|---|
| title | `title[0]` | `title` | `title` | `title` / `article_title` |
| authors | `author[]`(given/family) | `authorString`(分号逗号切分) | `authors[]` | `authors` / `creators` |
| year | `published.date-parts[0][0]` / `issued` | `pubYear` | `pubdate` | `year` / `publication_year` |
| journal | `container-title[0]` | `journalTitle` | `fulljournalname` / `source` | `journal` / `publication_title` |
| volume/issue/pages | `volume` / `issue` / `page` | `volume` / `issue` / `pageInfo` | `volume` / `issue` / `pages` | 同名或近似键 |
| doi | `DOI` / `identifier[]`(idtype=doi) | `doi` | `articleids[]`(idtype=doi) | `doi` / `identifier[]` |
| pmid/pmcid | `identifier[]` | `pmid` / `pmcid` | `uid` / `articleids[]` | `identifier[]` |
| issn | `issn-type[]` / `ISSN` | `issn` | `issn` | `issn` |
| doc_type | `type` | `pubType` | `pubType` | `type` / `document_type` |
| abstract | `abstract` | `abstractText` | `abstract` | `abstract` / `description` |
| publisher | `publisher` | `publisherName` | `publisher` | `publisher` |

归一规则：

- 年份：从日期串中提取 4 位年（1900–2099），无法解析为 `null`。
- DOI：统一小写、去 `https://doi.org/` 前缀与尾随标点；从标识符数组里按 `idtype=doi` 提取。
- 作者：`given family` 拼接；`authorString` 按 `;`/`,`/` and ` 切分；过滤 `et al.` 等占位。
- 缺失与未知分开：缺失填 `null`/`[]`，并写入 `missing_metadata` 与 `coverage.fields_missing_per_db`。

## 3. 去重规则（可解释、确定性）

两级匹配，前一级命中即停止（按此顺序）：

1. 标识符精确：DOI（规范化后）> PMID > PMCID；
2. 标题精确：标题规范化（去标点、小写、空白折叠）后全等；
3. 标题 + 一作 + 年份：三者均等。

聚类按记录输入顺序扫描，把每条记录并入**第一个**命中的已有簇，保证结果不依赖
排序随机性；簇的 `dedup_reason` 记录实际命中的规则（`doi` / `title` /
`title-author-year` / `unique`）。

## 4. 合并与冲突消解

合并记录字段取值优先级（低数字优先；同值去重后取排序最靠前者）：

| 字段 | 优先级（高 → 低） |
|---|---|
| doi | Crossref > PubMed > EuropePMC > 通用 |
| pmid / pmcid | PubMed > EuropePMC > Crossref > 通用 |
| abstract | EuropePMC > PubMed > Crossref > 通用 |
| publisher | Crossref > 通用 > EuropePMC > PubMed |
| 其余字段 | Crossref > EuropePMC > PubMed > 通用 |

作者取所有来源的并集并保持去重顺序；`identifiers` 取所有来源的并集。
当 `year/doi/volume/issue/pages/journal/title` 在不同来源出现不同取值时，写入
`conflicts[]`，记录双方值、`resolved=priority`、胜出值与来源。

每个字段的 `field_sources[]` 记录来源库、源文件与 locator；`provenance[]` 记录
每条成员记录的「库—源文件—locator—请求参数」链路；`raw_snapshot` 为原始响应的
逐字 JSON 片段（截断到 2400 字符）。

## 5. 覆盖比较

`coverage` 计算：

- 每个库的输入记录数 `db_input_counts`；
- 合并后记录数 `merged_record_count` 与去重合并数 `duplicates_merged`；
- 仅单库收录记录数 `records_in_single_db_only` 及其 `record_id`；
- 每个库在所有记录里系统性缺失的字段 `fields_missing_per_db`。

## 6. 相关性提示

`relevance_hint` 由查询词项在标题/摘要/期刊/关键词文本中的确定性命中计算；
命中给出 `matched query terms`，未命中给出 `none`。`ranking` 按命中数、年份、
`record_id` 三级排序，保证同输入稳定；模型仲裁可通过 `decisions.json` 覆盖。
