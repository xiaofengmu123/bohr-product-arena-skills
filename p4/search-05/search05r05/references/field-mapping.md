# 跨库字段映射与合并规则

本表定义各库原始字段到统一 canonical 字段的映射，以及合并时的取值优先级。映射实现在 `scripts/fedsearch.py`，本表供人工复核与复用。

## 1. 字段映射

| canonical | Crossref (`message.items[]`) | EuropePMC (`resultList.result[]`) | PubMed esummary (`result[uid]`) | 出版社/通用 |
|-|-|-|-|-|
| `title` | `title[0]` | `title` | `title` | `title`/`articleTitle`/`name` |
| `authors` | `author[]` (family/given) | `authorList.author[]` 或 `authorString` | `authors[]` (name) | `authors`/`author`/`creators` |
| `year` | `issued.date-parts[0][0]` | `pubYear` | `pubdate`/`epubdate` | `year`/`publication_year` |
| `journal` | `container-title[0]` | `journalInfo.journal.title` | `source`/`fulljournalname` | `journal`/`publicationName` |
| `volume` | `volume` | `journalInfo.volume` | `volume` | `volume` |
| `issue` | `issue` | `journalInfo.issue` | `issue` | `issue`/`number` |
| `pages` | `page` | `pageInfo` | `pages`/`elocationid` | `pages`/`firstpage` |
| `doi` | `DOI` | `doi` | `articleids[idtype=doi].value` | `doi`/`DOI` |
| `pmid` | — | `id`/`pmid` | `uid` | `pmid`/`PMID` |
| `pmcid` | — | `pmcid` | `articleids[idtype=pmc].value` | `pmcid`/`PMCID` |
| `issn` | `ISSN`/`issn-type[].value` | `journalInfo.journal.issn` | `issn` | `ISSN`/`issn` |
| `doc_type` | `type` | `pubType` | `pubtype[]` | `type`/`documentType` |
| `abstract` | `abstract` | `abstractText` | — | `abstract`/`description` |
| `publisher` | `publisher` | — | — | `publisher` |

## 2. 归一规则

- **DOI**：去 `https://doi.org/` 前缀、转小写；`10.1/A` 与 `10.1/a` 视为同一条。
- **ISSN**：去连字符/空格后大写，8 位补 `XXXX-XXXX`。
- **年份**：从日期串/`date-parts` 抽首个 4 位整数，校验 1000–2200。
- **作者**：统一为 `Family, Given`；支持 `"Doe, John"`、`"John Doe"`、`{family,given}` 三种写法；去重保留首次出现顺序。
- **文献类型**：`type`/`pubType` 多值用 `;` 连接，缺失填 `null` 而非空串，区分「未知」与「缺失」。

## 3. 去重规则（可解释的层级判定）

1. **DOI 精确**：归一 DOI 相同即同篇。
2. **PMID / PMCID 精确**：无 DOI 时命中任一标识符即同篇。
3. **标题 + 一作 + 年份**：归一标题（去 HTML/标点/大小写）相同、一作姓氏相同、年份相同。
4. **标题模糊**：标题 `SequenceMatcher` 相似度 ≥0.90 且年份差 ≤1。
5. **标题+年份精确**（作者缺省兜底）：归一标题相同且年份相同。

判定层级从上到下，命中即归并；每个簇保留一个 `_dedup_reason`，写入候选 `provenance` 供复核。

## 4. 合并优先级（冲突消解）

字段取值从高到低按库可信度排序，同一库多值取更长者：

- `doi`/`issn`/`title`/`journal`/`year`/`pages`/`volume`/`issue`/`publisher`：Crossref > EuropePMC > 出版社 > PubMed > generic
- `abstract`/`doc_type`：EuropePMC > 出版社 > Crossref > PubMed > generic
- `pmid`：PubMed > EuropePMC > 出版社 > Crossref
- `pmcid`：EuropePMC > PubMed > Crossref

胜出值写入 `field_sources`（如 `crossref@message.items[3]`），冲突的其它取值写入 `conflicts`，标注 `chosen_value` 与 `resolution`，不静默覆盖。标识符（doi/pmid/pmcid/issn/source_id）取并集。
