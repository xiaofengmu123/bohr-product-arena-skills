# 跨库字段映射与合并规则

本文是 `scripts/core.py` 的规范说明，供复核与复用；代码为准，本文用于解释
「为什么这样归一/去重/合并」。

## 1. 数据库响应识别（`classify_response`）

| 来源库 | 判别特征（大小写不敏感） | 记录位置 |
|---|---|---|
| Crossref | 顶层 `message.items[]` | `message.items[i]` |
| EuropePMC | 顶层 `resultList.result[]` | `resultList.result[i]` |
| PubMed | `esearchresult` / `esummaryresult` / `result.uids` | `result.{uid}` 或 `esearchresult.docsums[]` |
| 出版社/其他 | 其余异构 JSON | 递归扫描最大的 record 列表 |

记录判定（`_looks_like_record`）：dict 至少含 `title` / `doi` / `pmid` /
`pmcid` / `author(s)` 之一。抽取用「包含最多记录元素的列表」避免把
`container-title` 等元数据数组误判为记录。

## 2. 统一字段与各库键名映射（`normalize_record`）

统一字段取各库真实语义的键，不是机械拼接。表中顺序即取值优先级（先匹配到先取）。

| 统一字段 | Crossref | EuropePMC | PubMed | 出版社/其他 |
|---|---|---|---|---|
| `title` | `title[0]` | `title` | `title` | `title`/`articleTitle` |
| `authors` | `author[]` (`given`,`family`) | `authorList.author[]` / `authorString` | `authors[]` (`name`) | `authors`/`creator` |
| `year` | `issued.date-parts[0][0]` 等日期 | `pubYear`/`firstPublicationDate` | `pubdate`/`sortpubdate` | `year`/`publicationYear` |
| `journal` | `container-title[0]` | `journalInfo.journal.title`/`journalTitle` | `fulljournalname`/`source` | `journal` |
| `volume`/`issue`/`pages` | `volume`/`issue`/`page` | `journalInfo.volume`/`issue`/`pageInfo` | `volume`/`issue`/`pages` | 同名键 |
| `doi` | `DOI` | `doi` | `articleids[idtype=doi]` | `doi`/`prism:doi` |
| `pmid` | — | `pmid` | `uid`/`pmid` | `pmid` |
| `pmcid` | — | `pmcid` | `articleids[idtype=pmc]` | `pmcid` |
| `issn` | `ISSN[0]` | `journalInfo.issn` | `issn` | `issn`/`eissn` |
| `doc_type` | `type` | `pubType` | `pubtype[0]` | `documentType` |
| `publisher` | `publisher` | `publisher` | — | `publisherName` |
| `abstract` | `abstract` | `abstractText` | — | `abstract` |

## 3. 归一约定

- **DOI**：去掉 `https://doi.org/` / `doi:` 前缀，去首尾标点，统一小写。
- **标题**：剥离 HTML 标签、换行与标点，折叠空白、统一小写，得到
  `_norm_title` 用于模糊比较；`title` 字段本身保留原文。
- **作者**：统一为 `[{given, family, full}]`；`authorString` 按
  `;` `,` `and` 切分回填 `full`。一作键 = 小写折叠的 `family given`。
- **年份**：按日期字段优先级找 `18xx–20xx` 四位年份，找不到填 `null`
  （写进 `missing_metadata`，不猜）。
- **缺失与未知**：缺失统一填 `null` / `[]`；字段值从未出现才记
  `fields_missing`，绝不写成 `""` 或占位符冒充数据。

## 4. 去重（`dedup_records`，确定性、可解释）

按顺序执行，全部记录同时进入并查集，原因写入 `dedup_reason`：

1. **DOI 精确**（归一后相等）→ `doi_exact`
2. **PMID / PMCID 精确** → `pmid_exact` / `pmcid_exact`
3. **标题 + 一作 + 年份**（三者同时非空且相等）→ `title_firstauthor_year`
4. **标题模糊**：`difflib.SequenceMatcher.ratio() >= 0.90` 且年份不冲突 →
   `title_fuzzy_0.90`

说明：1–3 是强信号，4 是弱信号（仅同年）；每次并集都记录
`matched_fields`，因此「为什么是同一篇」可以逐条复核。人工可仲裁入口：
`/app/work/decisions.json` 的 `ranking` / `limitations` / `pending_human`。

## 5. 合并（`merge_cluster`，稳定、可重复）

- 每个统一字段独立选主值：按 `FIELD_PRIORITY`（db 顺序）→ 簇内输入顺序，
  先到先得；`core.py` 的 `FIELD_PRIORITY` 是唯一事实来源。
- 主值来源写进 `field_sources[field] = {value, db, source_file, locator}`。
- **冲突消解**：同一字段不同来源取值不同时，全部取值连同来源写进
  `conflicts`，主值标 `resolved_to`，不静默丢弃。
- 每条候选保留 `member_records[]`：每个成员含 `record_id / db / source_file
  / locator / request / raw_snapshot`，`raw_snapshot` 是原始节点的确定性
  JSON 再序列化（字段值逐字，不改写），>4000 字符时截断并标
  `raw_snapshot_truncated=true`。

## 6. 覆盖比较（`build_coverage`）

- 每库：`record_count`、`fields_missing`（该库全部记录都缺的统一字段，
  即系统性缺失）、`single_db_only`（只在该库出现的合并记录数）。
- 全局：`total_input_records`、`merged_record_count`、`duplicates_merged`、
  `records_in_single_db_only`。
- 缺口显式列出，不静默返回不完整清单。

## 7. 证据溯源（`build_evidence`）

每条候选的每个原始成员生成一条 `evidence_records`：`evidence_id / type /
db / source_file / locator / quote / record_id`，`quote` 是原始节点 JSON
前缀（逐字，非转述）。他人可按 `source_file + locator` 回到原始响应核对。

## 8. 相关性提示与排序

`relevance_hint` 是确定性词项命中（研究问题 + 查询派生词在
标题/摘要/期刊中出现次数）：≥2 命中 `high`，1 命中 `medium`，否则 `low`。
默认 `ranking` 按 `relevance_hint` 降序、`record_id` 稳定排序；模型的
人工相关性判断通过 `decisions.json` 覆盖，不写进脚本。
