# 规则与可复用资源（rules.md）

本文件沉淀 sci-library-organizer 的全部可复用规则：目录规范、命名模板、分类词表、字段映射、
去重与版本判定、冲突与降级。所有规则均可被 `scripts/main.py` 复现，不硬编码到单一案例。

## 1. 目录层级（hierarchy）

`--hierarchy` 指定维度，逗号分隔，顺序即目录层级顺序。可用维度：

| 维度 | 取值来源 | 缺省回退 |
|---|---|---|
| `document_type` | 元数据类型 > 文件名信号 > 后缀 kind | `unknown_publication` / `other` |
| `topic` | 受控主题词表命中标题/文件名/全文 | `unclassified` |
| `year` | 元数据 > 全文 > 文件名（4 位年份） | `undated` |
| `kind` | 后缀 → kind（pdf/bibliography/json/dataset/image/…） | `other` |

默认 `document_type,topic`。示例路径：`journal_article/quantum/`、`preprint/machine_learning/`。
路径段经 `slugify` 清洗（保留 Unicode、替换路径非法字符、限长 60）。

## 2. 文档类型分类（document_type）

优先级：**元数据条目类型（BibTeX/RIS/JSON）> 文件名信号 > 后缀 kind**。

- BibTeX/RIS 类型映射：`article→journal_article`、`inproceedings/conference→conference_paper`、
  `phdthesis/mastersthesis→thesis`、`techreport→report`、`unpublished→preprint`、
  `book→book`、`incollection→chapter`、`dataset→dataset`、`software→software`。
- 文件名信号：`thesis/dissertation`、`proceedings/workshop/conf`、`preprint/arxiv/manuscript`、
  `supplement/appendix`、`report`、`book`、`dataset`、`scan/ocr`。
- 后缀 kind 兜底：`.bib/.ris→bibliographic_record`、`.json/.xml/.yaml→metadata_record`、
  `.md/.txt→note`、`.csv/.tsv→dataset`、`.png/.jpg→scan`、`.py→software`。
- PDF 无任何信号时写 `unknown_publication`（**不臆测**），计入 `uncertainty.unclassified`。

## 3. 主题标签（topic，受控词表）

主题来自 `scripts/core_impl.py` 的 `TOPIC_TERMS`（15 组同义合并词表），命中标题、文件名与
全文前 2000 字符；每文件最多 3 个标签，无命中 → `unclassified`。避免标签爆炸：同义词并入
同一主题键；新增领域主题只需在 `TOPIC_TERMS` 追加模式，无需改逻辑。

## 4. 自动命名模板

模板：`{第一作者姓}-{年份}-{标题}.{后缀}`；字段缺失的降级规则：

| 缺失 | 处理 |
|---|---|
| 作者 | 用 `anon` |
| 年份 | 用 `na` |
| 标题 | 用原文件 stem（经 slugify） |
| 全部为空 | 保留原文件名（经 slugify） |

清洗规则：Unicode NFC 归一；替换 `/ \ : * ? " < > |` 与控制字符为 `_`；折叠连续空格与下划线；
标题段限 120 字符、全名限 180 字符。同名不同内容 → `-N` 后缀（`-2/-3/…`），不覆盖。

## 5. 字段映射（元数据抽取）

| 目标字段 | 来源优先级 | evidence 方法 |
|---|---|---|
| title | 元数据记录 > 全文首段启发 > 文件名 stem | metadata_record / text_heuristic / filename_heuristic |
| authors | 元数据记录 > 文件名首段启发 | metadata_record / filename_heuristic |
| year | 元数据记录 > 全文 4 位年份 > 文件名 | 同上 |
| journal | 仅元数据记录 | metadata_record |
| doi | 元数据记录 > 全文 DOI 正则 > 文件名 | 同上 |
| arxiv_id | 元数据记录 > 全文 arXiv 正则 > 文件名 | 同上 |

每个字段值都携带 `evidence:[{locator, quote, method}]`；无证据字段写 `not_reported`，
绝不虚构。`decisions.json` 外部覆盖必须带 `evidence` 列表，否则进入 `pending_human` 不采纳。

## 6. 去重与版本判定

- **字节级去重**：SHA-256 完全一致 → 后者 `duplicate_of` 前者（排序后首项为保留项）。
- **版本组（并查集）**：按 `doi`（精确）→ `arxiv_id`（精确）→ `norm(title)+year` 聚合。
- **版本角色**：仅当文件名/标题出现标记时判定——`preprint`、`published/final`、
  `supplementary/supporting`、`revision/v2`；否则角色为 `null`（未知，不臆测）。
- **归一化键**：NFKD 去重音、去所有非字母数字、转小写；用于标题+年份匹配。

## 7. 证据溯源（硬要求）

- 每个关键结论/数值/字段带 `locator + quote`：`locator` 为 `<相对路径>:<来源层>`，
  `quote` 为原文逐字片段（上限 300 字符，哈希场景为 `<sha256>`）。
- 冲突信息不隐藏：同名不同内容、同一指标两来源不一致，都记录并标注（`source_conflict` /
  `conflicts[]`），缺失写 `not_reported` 或 `pending_human`。

## 8. 失败场景→字段映射

| 场景 | 写入位置 | status |
|---|---|---|
| 不可读 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（替换字符） | 文件条目 `encoding` + `degradations` | partial |
| 加密 PDF / 无文本层扫描件 | `has_text_layer=false` + `uncertainty.no_text_layer` | partial |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删 |
| 缺 pdftotext/pdfinfo/OCR | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |

## 9. 中间表示 schema（可复核/可重放）

- `work/inventory.json`：`{files:[{source, kind, sha256, size_bytes, permissions, encoding,
  text_preview, has_text_layer, is_input_spec}], dirs, totals}`——纯事实，无推断。
- `work/decisions.json`：`{entries:[{source, kind, doc_type, topics, version_role,
  fields:{...带 evidence}, has_text_layer}], pending_decisions}`。
- `work/plan.json`：`{hierarchy, version_groups, decisions:[{id, source, action,
  document_type, topics, version_group, category_path, suggested_name, target_relpath,
  duplicate_of, basis, evidence, state, conflicts}]}`——落地前可审计。
- `work/rollback.json`：`{created:[{target, source, sha256, op}], already_present,
  input_untouched}`——删除 `created` 即回滚。

## 10. 边界与扩展

- 仅标准库；`pdftotext/pdfinfo` 为可选增强（预抽取文本放 `work/text/<sha256>.txt`）。
- 新增文件类型：在 `EXT_KIND` 加后缀；新增领域：在 `TOPIC_TERMS` 加模式；规则不变。
- 确定性：无随机量、无网络、无绝对路径写入结果（除 locator 相对路径），同输入必同输出。
