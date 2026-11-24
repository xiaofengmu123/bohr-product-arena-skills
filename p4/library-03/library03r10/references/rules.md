# 规则与复用说明（sci-library-curator）

本文是 `scripts/main.py` + `scripts/core_impl.py` 的可复用规则表；与他人共享时按本说明配置即可复现。

## 1. 目录层级（`--hierarchy`）

维度任意组合，逗号分隔，默认 `document_type,topic`。可用维度：

| 维度 | 取值来源 | 缺省回退 |
|---|---|---|
| `document_type` | BibTeX entrytype > 扩展名/magic > 文件名标记 > decisions | `unknown` |
| `topic` | 仅 decisions（须逐字语录）或 BibTeX `keywords` | `not_reported` |
| `year` | BibTeX `year` 或 decisions（须逐字语录） | `unknown` |
| `type` | 同 `document_type`（别名，兼容旧题面） | `unknown` |

每个组件经 `sanitize_comp`（NFKC 归一、`\/:*?"<>|` 与控制字符替换为 `_`、去首尾空格与点、超长截断）。

## 2. 文档类型分类（结构分类 ≠ 元数据臆造）

优先级从高到低，且只取「可指回来源」的信号：

1. `bibtex`：`.bib`/`.ris` 条目的 entrytype（article→journal_article，inproceedings→conference，phdthesis→thesis，unpublished/preprint→preprint，misc 等）。
2. `extension`：`.csv/.tsv/.xlsx/.json/.yaml→dataset，`.md/.txt/.rst→note`，`.tex→source`，`.bib/.ris→bibliography`，图片→scan，`.zip/.tar/.gz→archive`，`.pdf`→以 magic 判为 PDF（subtype unconfirmed）。
3. `magic`：`%PDF-`、PNG/JPEG/GIF/TIFF、zip/gzip/bz2/xz。
4. `filename`：仅结构标记（preprint/conference/thesis/supplementary/dataset/note），**不作为**标题/作者/年份/DOI 依据。
5. `decision`：来自 `decisions.json` 且 quote 逐字命中。

主题（topic）与元数据（title/authors/year/journal/doi/arxiv_id）**绝不**由文件名或扩展名猜测；只来自 BibTeX/RIS 原文或逐字语录命中的 decisions。

## 3. 自动命名（`--template`）

默认模板 `{first_author}-{year}-{short_title}`。可用占位符：`{first_author}`、`{year}`、`{title}`、`{short_title}`、`{doi}`、`{doc_type}`、`{authors}`（前两位）。

降级链：`first_author` 缺失→`unknown`；`year` 缺失→`unknown`；`short_title` 取标题前 60 字符并 `sanitize_comp`，缺失→`untitled`。多语言标题保留 UTF-8（允许中文），仅替换路径非法字符。同名异内容自动 `-N` 后缀，不覆盖。

## 4. 去重与版本

- 字节级重复：SHA-256 判同；每组只保留 `source` 字典序最小者为 `keep=true`，其余 `duplicate_of` 指向保留者、`keep=false`。
- 版本组：并查集按键聚合——`doi:<归一化DOI>`、`arxiv:<归一化arXiv>`（去 `vN` 后缀）、`title:<归一化标题>|<year>`（标题 NFKD 去音调、小写、仅字母数字）。组 id `vg-NNN`（按成员最小 source 排序）。`preprint/published/supplementary/revision` 仅在原文有标记（BibTeX entrytype 或逐字语录）时判定，否则 `version_role=null`。

## 5. decisions.json 结构（Agent 语义判断产物）

```json
{
  "version": 1,
  "files": {
    "papers/paper.pdf": {
      "assignments": {
        "doc_type": {"value": "preprint", "evidence": [{"locator": "papers/paper.pdf:1", "quote": "A preprint submitted to arXiv"}]},
        "topic": {"value": "quantum", "evidence": [{"locator": "papers/paper.pdf:3", "quote": "quantum error correction"}]},
        "metadata": {"value": {"title": "Exact Title", "authors": ["A. Author"], "year": "2024", "doi": "10.1000/xyz"}, "evidence": [{"locator": "papers/paper.pdf:1", "quote": "Exact Title"}]}
      }
    }
  }
}
```

- 键为相对路径（`/` 分隔），字段名与 catalog 一致：`doc_type`/`topic`/`metadata`/`tags`/`has_text_layer`/`version_role`。
- 每条断言必须带 `evidence[].quote`，脚本将该 quote 与该文件可见文本（文件名 + 抽取全文 + BibTeX 原文）做**逐字子串**比对。
- 逐字命中才采纳；否则写入 `pending_human`，字段不采纳，绝不覆盖机器值。

## 6. 状态判定（`status`）

| 条件 | status |
|---|---|
| 输入目录缺失 | `input_error` |
| 输入存在但材料文件为 0 | `insufficient_evidence` |
| 存在 pending_human / degradations / errors / permission_errors / conflicts / skipped | `partial` |
| 以上皆无 | `ok` |

## 7. 字段映射（BibTeX/RIS → metadata）

BibTeX 字段：`title`、`author`（`and` 拆分）、`year`/`date`（取首个四位年）、`journal`/`booktitle`、`doi`、`eprint`+`archiveprefix=arXiv`→`arxiv_id`、`keywords`→tags。
RIS 行：`TY/AU/T1/PY/JO/DO/KW`；`TY` 映射 JOUR→article、CONF→inproceedings、THES→phdthesis、DATA→dataset 等。

## 8. 证据与诚实性

- 每字段依据写入 `metadata_basis`（`bibtex`/`decision`）与 `classification_basis`。
- 无证据字段 `null`/`not_reported`；文本抽取未做时 `has_text_layer=null`，抽取为空时 `has_text_layer=false`。
- 失败如实记录到 `failures.*` 与 `degradations`，不把未完成写成完成。
