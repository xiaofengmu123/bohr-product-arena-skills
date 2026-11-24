# 规则与规范（可复用）

本文档是 `sci-library-organizer` v2 的规则源，脚本与 `SKILL.md` 均指向此处。所有规则以「可复核、可回滚、不编造、证据可溯源」为前提。

## 1. 目录层级规范

- 默认维度 `document_type`，可用 `--hierarchy document_type,topic` 扩展为多级（也支持 `year`/`type`）。
- 顶层目录取 `catalog[source].document_type`，取值：
  `journal_article | preprint | conference_paper | thesis | supplementary | dataset | scanned | bibliography | notes | other | unknown`。
- 无证据时目录为 `unknown/`，并在 `uncertainty.unclassified` 与 `pending_human` 记录，不硬塞类别。
- 目标路径 `<library>/<category_path>/<suggested_name>`；`--apply` 才创建（复制非移动），`/input` 永不改动。
- `case.json`/`task.md` 记为 `role=control`，进入 catalog 但不复制到 library，操作日志标 `keep_control`。

## 2. 命名模板

- 模板：`<first_author>-<year>-<title>.<ext>`；字段缺失逐级降级为 `unknown`/`noyear`/`unnamed`。
- 特殊字符（`\ / : * ? " < > |` 与控制符）替换为空格；连续空白合并；首尾点与空格去除；标题截断 80 字符。
- 多作者只取第一作者姓氏（`author.split(',')[0]`；"First Last" 取末词）；多语言标题统一 UTF-8 落盘。
- 同名不同内容（哈希不同）自动加 `-2`/`-3` 后缀，不覆盖；同名同哈希标 `already_present`。

## 3. 标签归一

- 标签只来自：BibTeX `keywords`、文本 `keywords:`/`关键词:` 行、decisions 的 `labels`/`topic_labels`，以及 `type:<document_type>` 派生标签。
- 归一：小写、只保留字母/数字/CJK，按归一后键去重，保留首个原始形式；单条最多 20 个，避免标签爆炸。
- 每个标签在 `evidence_map` 中有 `locator + quote`；同义合并以归一键为准。

## 4. 去重与版本

- 字节级重复：SHA-256 相同即重复；按 `source` 字典序取第一个为 `keep`，其余 `duplicate_of` 指向它，不删除原文件。
- 版本组：按 `doi:<doi>`、`arxiv:<id>`、`title+year:<归一化标题>|<年份>` 聚合（并查集连接）；组内成员 `version_group=vg-NNN`。
- 版本角色：`preprint/published/supplementary/revision` 只在原文/文件名出现对应标记时判定，否则 `unknown`；`supplementary` 不并入正文去重。

## 5. evidence_map schema（证据溯源强化核心）

每条结论一条 `claim`：

```json
{
  "claim_id": "c0001",
  "claim": "人类可读结论",
  "subject": "所指文件 source（相对路径）",
  "field": "document_type/labels/doi/arxiv_id/duplicate_of/version_role/suggested_name/...",
  "value": "结论值（标量或列表）",
  "basis": "filename | pdf_text | text | bibtex | ris | sha256 | decisions | rule | extension",
  "locator": "可回查定位，如 <relpath>::pos=a-b 或 <relpath>::bibkey=K::field=F",
  "quote": "逐字原文片段 / 匹配子串 / 哈希值（必须非空）",
  "method": "regex | title_match | hash | marker | decisions | rule | extension",
  "confidence": "high | medium | low",
  "verified": true,
  "note": "可选说明"
}
```

- `catalog[source].claims` 引用 `claim_id`，形成「条目 ↔ 证据」双向链；`evidence_map.by_subject` 反向索引。
- `quote` 为空时该条置信度降为 `low`、`verified=false`、`quote="not_reported"`，绝不伪造。
- 冲突不隐藏：同一字段两处来源不一致时两条都记录，并在 `uncertainty.has_source_conflict`/`failures.conflicts` 标 `source_conflict`。

## 6. decisions.json 结构（模型补充，可选）

```json
{
  "items": [
    {"source": "papers/paper.pdf", "field": "title", "value": "A Study",
     "locator": "papers/paper.pdf::page=1::pos=0-30", "quote": "A Study of ...",
     "confidence": "high"}
  ]
}
```

- 支持字段：`title/authors/year/journal/doi/arxiv_id/document_type/labels/topic_labels/version_role/note`。
- 只采纳 `locator` 与 `quote` 均非空的条目；否则进入 `pending_human` 与 `failures.skipped`。

## 7. 字段映射

| 来源字段 | catalog.metadata | 提取方式 |
|---|---|---|
| BibTeX `title/author/journal/year/doi` | title/authors/journal/year/doi | 逐字 quote + 归一化标题匹配 |
| RIS `AU/PY/TI/DO` | authors/year/title/doi | 逐行 quote |
| 文本 `arXiv:` / `10.xxxx/...` / 4 位年份 | arxiv_id/doi/year | 正则匹配，quote=匹配子串 |
| 文件名/文本类型标记 | document_type | marker 匹配，quote=匹配子串 |

未提取字段一律 `not_reported`，写入 `uncertainty.missing_metadata_fields`。

## 8. 失败场景映射

| 场景 | 字段 | status |
|---|---|---|
| 不可读/stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码/替换字符 | 条目 `text_errors` + `degradations` | partial |
| 加密 PDF/扫描件无文本层 | `has_text_layer=false` + `failures.skipped` | partial |
| 同名不同版本 | `failures.conflicts` + 后缀日志 | 不误删 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |
| 缺 `pdftotext` | `degradations`，保留清单与哈希 | 不影响清单 |
