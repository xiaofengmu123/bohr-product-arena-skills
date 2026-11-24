# 字段映射、去重与合并规则

本文说明 `scripts/fedcore.py` 的确定性语义，供他人复核与复用。所有规则只依赖输入文件内容与固定优先级，不依赖 dict 迭代顺序、本地时区或随机数。

## 1. 响应结构识别（`classify_response`）

| 信号 | 库 | 记录定位路径 |
|---|---|---|
| `message.items[]` | Crossref | `message.items[i]` |
| `resultList.result[]` | EuropePMC | `resultList.result[i]` |
| `result.uids[]` + `result.<uid>` | PubMed esummary | `result.<uid>` |
| `esearchresult.idlist` | PubMed esearch（无文献记录，按空响应处理） | — |
| 其他带 `doi/title/author` 的字典列表 | 出版社 / generic | `<listKey>[i]` 或 `[i]` |

## 2. 跨库字段归一（`normalize_record`）

统一字段：`title / authors / year / journal / volume / issue / pages / doi / pmid / pmcid / issn / doc_type / abstract / publisher / identifiers`。

- **authors**：统一为 `"Family, Given"` 字符串列表；EuropePMC 优先用 `authorList`，缺失时回退解析 `authorString`。
- **year**：所有库统一为 `int`；从 `date-parts`、`pubYear`、`pubdate`、`issued` 等按库取首个 `(18|19|20)xx`。
- **title / abstract**：剥 HTML/JATS 标签、折叠空白；缺失填 `null`，**不填 `"unknown"`**（缺失与未知严格区分）。
- **identifiers**：从 `doi / pmid / pmcid / issn / pii / isbn` 收集成 dict，值统一为字符串；DOI 一律小写去空白。
- 缺失字段一律 `null`，逐条记入 `missing_metadata`，不编造。

## 3. 去重（`dedup_reason`）

判定优先级（命中即等价，返回可解释依据字符串）：

1. `doi_exact`：DOI 归一化后相等；
2. `pmid_exact`：PMID 相等；
3. `title_author_year`：标题规范键相等 + 一作姓氏相等 + 年份相等；
4. `title_fuzzy(≥0.90)`：标题 `SequenceMatcher` 比例 ≥ 0.90 且同年。

去重用并查集做传递闭包；簇内顺序与簇间顺序均由「规范标题 + 定位符」排序，结果与输入排序无关。每条合并记录在 `provenance.dedup_method` 列出实际命中的理由，保留 `duplicate_of_source_records` 供人工仲裁。

## 4. 合并与冲突消解（`merge_cluster`）

- 字段取值优先级：**Crossref > EuropePMC > PubMed > generic/出版社**，同库内按定位符顺序取首个非空值；`field_sources` 记录每个字段的所有取值来源。
- 同一字段出现多个语义不同非空值 → 记入 `conflicts`，并声明 `resolution = priority_order`。
- `abstract` 例外：取**最长**非空摘要（可解释、确定性最强）。
- `identifiers` 取并集（保留每库能提供的标识符，不覆盖）。
- `provenance.raw_snapshots` 保存每个来源记录的**逐字 JSON 片段**（≤2400 字符），供逐条核对。

## 5. 覆盖比较（`build_coverage`）

- `total_input_records` = 解析到的源记录数；`merged_record_count` = 合并后候选数；`duplicates_merged = total - merged`。
- `records_in_single_db_only`：仅一个库收录的合并记录 ID（覆盖盲区显式化）。
- `per_database[].fields_missing`：该库在所有关键字段中始终缺失的字段集合。
- `systematically_missing_fields`：在所有合并记录中都缺失的字段集合。

## 6. 证据溯源（`build_evidence`）

每条证据 `evidence_id / record_id / db / source_file / locator / query / quote`，其中 `quote` 是原始响应逐字片段，可沿「库—查询—原始响应—映射—去重—合并」链路复核。

## 7. 确定性保证

- 文件遍历按相对路径字典序；所有集合使用 `sorted(key=...)` 稳定排序。
- 候选排序固定：`-relevance_score → -year → title_key → record_id` 全序，无平局留给随机性。
- 结果中仅 `runtime.started_at / finished_at / seconds` 与 `change_log[].at` 为运行时刻真值；`compute_fingerprint` 对去除这些易变字段后的结果求 SHA256，`runtime.content_fingerprint` 供两次运行比较内容一致性。
- 同输入两次运行，`_strip_volatile` 后全等（由 `selftest.py` 校验）。