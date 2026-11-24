# 规则与依据（sci-library-organizer）

本文件把脚本行为固定成可复用、可复核规则。脚本只实现本文件列出的规则；
实现改动应同步更新本文件。所有结论都以「locator + 逐字 quote」或 SHA-256 可核对。

## 1. 目录层级（hierarchy）

- `--hierarchy` 接受固定维度：`document_type`、`topic`、`year`、`file_type`、`status`，
  逗号分隔，顺序即目录层级顺序，默认 `document_type,topic`。
- 每层取值由该文件对应字段决定；缺失降级为 `unclassified` / `uncategorized` /
  `unknown_year` / `unknown_type` / `unknown_status`，不硬猜。
- `category_path` 是相对 `library/` 的路径段列表；`library/<seg1>/<seg2>/.../`。

## 2. 文件类型识别（file_type）

| 信号 | 类型 | 依据 |
|---|---|---|
| 扩展名 `.pdf` 或魔数 `%PDF-` | pdf | explicit（扩展名/魔数） |
| `.doc/.docx/.ppt/.pptx/.xls/.xlsx` | office_doc/office_slides/office_sheet | explicit |
| `.bib/.ris/.enw` | bibtex/ris/endnote | explicit |
| `.txt/.md/.csv/.tsv/.json/.xml/.tex/.ipynb` | text 族 | explicit |
| `.png/.jpg/.tif` 等 | image（疑似扫描件） | inferred |
| 其他 / 不可读 | other / unreadable | insufficient |

## 3. 文献类型（document_type）判定优先级

1. **explicit**：BibTeX entry type 映射（`article→journal_article`，
   `inproceedings→conference_paper`，`phdthesis/mastersthesis→thesis`，
   `preprint→preprint`，`techreport→report`，`book→book`）。
2. **filename**：文件名含 `supplement/supporting/thesis/dissertation/preprint/arxiv/
   conference/icml/neurips/.../dataset` 等关键词。
3. **text**：抽取文本含上述关键词（用于无文件名线索的 PDF）。
4. 其余 `unclassified_pdf` / `scanned` / `other`，**不**凭空判定。

判定依据写入 `field_basis.document_type`（`explicit|filename|text|insufficient`）。

## 4. 标签归一与主题

- 标签来源：BibTeX/RIS `keywords`、PDF 内嵌 `Keywords`、标题/正文中的受控词表命中。
- 归一：小写、去标点、合并空格；同义词表合并（如 `neural networks→neural-networks`、
  `llm→large-language-models`、`nlp→natural-language-processing`），去重排序。
- 主题 `topic` = 归一标签首位；无标签时为 `uncategorized`，避免标签爆炸与硬猜。

## 5. 元数据字段与证据强度（field_basis）

| basis 值 | 含义 | 来源示例 |
|---|---|---|
| explicit | 结构化/显式来源，最强 | BibTeX 字段、RIS 字段、PDF 内嵌元数据、正文中显式出现的 DOI/arXiv |
| inferred | 由文件名或正文首部推断，弱 | 首行标题猜测 |
| not_reported | 未提取到证据 | 字段值 `null`，写入 `limitations` 与 `uncertainty` |

- 字段：`title`、`authors`、`year`、`journal`、`doi`、`arxiv_id`、`keywords`、`venue_type`。
- DOI 用 `10.\d{4,9}/...` 正则提取；arXiv 用 `arXiv:NNNN.NNNNN(vN)?` 提取。
- 无证据字段**绝不**补造；`not_reported` 是诚实结果而非失败。

## 6. 命名模板（suggested_name）

- 当 `title` / `authors` / `year` 至少一项有证据时：
  `{first_author}-{year}-{title}[-{doi_suffix}].{ext}`。
- 首作者取 `authors[0]` 的姓氏段；年份缺失为 `unknown`；标题截断 80 字符。
- 非法字符 `\/:*?"<>|` 与控制符替换为 `_`；连续空白折叠；末尾 `.` 去除；保留 CJK。
- 元数据完全不足时**保留清洗后的原文件名**（`naming_mode=original_basename`），
  不生成 `unknown-year-untitled` 的伪命名，保证可回查原文件。

## 7. 字节级去重与版本分组

### 7.1 去重（SHA-256）

- 相同 SHA-256 的文件视为字节级重复；保留「路径最短、字典序最靠前」者，
  其余 `duplicate_of` 指向保留件、`keep=false`。
- 操作日志 `dedupe_mark` 记录 source/duplicate_of/sha256，可复核。

### 7.2 版本分组（并查集）

按以下键聚合，任一键相同即同组：

1. `doi:<归一化 DOI>`（小写、去尾标点）；
2. `arxiv:<arXiv 基号>`（去掉 `vN`）；
3. `title:<归一化标题>|<首作者小写>|<年份>`（标题去标点、去版本词，长度≥8 才启用）。

- 组内 `version_status`：`published > preprint > revision > supplementary > unknown`
  优先保留；其余 `version_of` 指向保留件、`keep=false`。
- `version_status` 仅当原文/文件名含 `supplement/preprint/arxiv/published/
  version of record/revision/v2` 等显式标记时判定，否则 `unknown`。

## 8. 落盘与幂等

- `--apply` 才复制文件到 `/app/output/library/`；缺省为干跑（只写计划）。
- 绝不修改 `/input`；整理 = 复制 + `rollback.json`（记录本次创建的相对目标路径）。
- 同名不同内容：目标名已存在且 SHA-256 不同 → 追加 `-2`、`-3` 后缀，不覆盖；
  同内容 → `already_present`，不重复复制。重复运行幂等。

## 9. status 判定

| status | 条件 |
|---|---|
| input_error | `/input` 目录缺失 |
| insufficient_evidence | 目录存在但无文件，或仅有 `case.json`/`task.md` 等输入上下文、无科研资料 |
| partial | 有资料文件但存在 `errors`/`degradations`/`pending_human`/`permission_errors`/`skipped`/`conflicts` |
| ok | 覆盖全部文件，无上述未决项；缺失元数据以 `not_reported` 如实标注（不视为未完成） |

## 10. 证据溯源格式（locator）

| locator 形式 | 含义 |
|---|---|
| `path:<rel>` | 文件相对路径 |
| `bibtex:<key>` / `ris:entry` | 书目条目 |
| `field:<name>` | 元数据字段 |
| `pdf-meta <Field>: <value>` | PDF 内嵌元数据（写进 quote） |
| `document_type:<rel>` / `label:<rel>` | 分类与标签判定 |

- 每条证据 `{locator, quote, basis}`；quote 为材料中**逐字**片段，截断 300 字符。
- 冲突信息两处都记录并标 `source_conflict`。

## 11. 降级映射（失败场景 → 字段）

| 场景 | 写入位置 | status 影响 |
|---|---|---|
| 文件不可读 / stat 失败 | `failures.permission_errors` + `skipped` + 条目 `errors` | partial |
| 坏编码（latin-1 回退） | 条目 `limitations` + `degradations` | partial |
| 加密 PDF / 无文本层 | `has_text_layer=false` + 条目 `limitations`（元数据 `not_reported`） | 不影响 ok |
| pdftotext/pdfinfo 缺失 | `degradations` + 条目 `limitations` | partial |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删，不误覆盖 |
| 人工决策无证据 | `pending_human`（字段不采纳） | partial |
