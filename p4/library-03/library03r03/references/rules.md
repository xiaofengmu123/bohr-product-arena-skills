# rules.md — 规则与可复用规范

本文档是 `sci-library-curator` 的组织规则说明。脚本不把规则硬编码成单一分支，而是依据本表判定；他人可据此复用或替换词表。每一条规则都要求在 `result.json` 中留下 `locator + quote` 或 `sha256` 作为证据，无证据的字段进入 `pending_human`，不采纳。

## 1. 目录层级（hierarchy）

`--hierarchy` 参数决定目录层级维度，逗号分隔，可任意组合：

| 维度 | 取值来源 | 缺失降级 |
|---|---|---|
| `document_type` | 受控词表分类 | `unknown` |
| `topic` | decisions.topic 或文件名强信号 | `unclassified` |
| `year` | 元数据年份 | `unknown_year` |
| `version_role` | preprint/published/supplementary/revision，仅原文有标记时判定 | `unknown` |

`document_type` 受控词表：`journal_article / conference_paper / preprint / thesis / supplementary / dataset / reference_metadata / notes / scanned_document / figure_image / code / archive / office_document / task_contract / other / unknown`。分类优先级：带证据的 `decisions.doc_type` > BibTeX entry type > 扩展名 + 文件名强信号（正则见 `scripts/core_impl.py`）> `unknown`。文件名归类必须把匹配片段作为 `quote` 回填。

## 2. 自动命名模板

命名只使用「已提取且可归因」的字段，按优先级降级；缺字段绝不补造：

1. `{第一作者姓}_{年份}_{标题slug}{扩展名}`（需 title + authors + year 三者齐备）；
2. `doi_{DOI}`（有 DOI）；
3. `arxiv_{arXivID}`（有 arXiv ID）；
4. `{原始文件名base}{扩展名}`（字段缺失，保留原名不臆测）。

清洗规则（`sanitize_component`）：NFKC 归一化；保留字母数字、CJK、`-`、`_`；其余字符折叠为单个 `_`；剔除首尾分隔符；主名超长截断 120 字符、标题 slug 80 字符。同名不同内容以 `-1/-2/...` 后缀解决，日志写入 `failures.conflicts`。

## 3. 去重与版本区分

- **字节级重复**：SHA-256 完全一致判定为重复。首次出现（按相对路径稳定排序）为 `keep=true`；后续条目 `duplicate_of` 指向首件，`--apply` 时不复制，仅记 `deduplicate` 操作日志。
- **版本组**：并查集（union-find）按以下强键聚合：`doi:`、`arxiv:`、归一化标题 + 年份（`titleyear:`）。只有 ≥2 个成员才生成版本组，单文件不虚报。
- **版本角色**（preprint/published/supplementary/revision）**只在原文或 decisions 带明确标记时判定**，否则 `unknown`。
- 冲突（同名不同内容）两边都记录：`failures.conflicts` 记目标名与全部来源，`operations` 记后缀化解结果。

## 4. 元数据字段映射

来源优先级：带 `evidence` 的 `decisions.json` > BibTeX/RIS/JSON 强信号 > `/app/work/text/<sha256>.txt` 预抽文本（PDF）。映射表：

| 字段 | BibTeX | RIS | 预抽文本 |
|---|---|---|---|
| title | `title={...}` | `TI  -` | 由模型/外部工具抽取后写 decisions |
| authors | `author={...}`（`and` 分割） | `AU  -` 多行 | 同上 |
| year | `year={...}` | `PY  -` 正则取 4 位 | 同上 |
| journal | `journal={...}` | `JO  -` / `T2  -` | 同上 |
| doi | 正则 `10.xxxx/...` | `DO  -` | 正则 `10.xxxx/...` |
| arxiv | — | `SN  -`/note 中正则 | `arXiv:xxxx.xxxx` 或 `arxiv.org/abs/...` |

任何字段无证据即 `not_reported`；整条决策无 `evidence` 字段则进入 `pending_human`，不采纳。

## 5. 标签归一

从 decisions + 文件名强信号生成标签，避免标签爆炸：小写化、NFKC、空白折叠为单空格，命中同义词表替换为规范形（如 `ml/machine learning` → `machine-learning`，`llm` → `language-model`）。标签集合排序后去重；保留地点/粒度不做上下位推断（缺领域本体的部分如实标注 `uncertainty`）。

## 6. decisions.json schema

```json
[
  {
    "source": "papers/foo.pdf",
    "doc_type": "journal_article",
    "topic": "protein folding",
    "tags": ["structure-prediction"],
    "title": "Example Title",
    "authors": ["Doe, J."],
    "year": 2024,
    "journal": "Nature",
    "doi": "10.xxxx/yyyy",
    "arxiv": "2410.12345",
    "version_role": "published",
    "version_group": "doi:10.xxxx/yyyy",
    "evidence": [{"locator": "papers/foo.pdf#page1", "quote": "逐字片段"}]
  }
]
```

脚本只采纳带 `evidence` 的字段；`source` 使用相对 `/input` 的斜杠路径，必须能与盘点结果对齐。

## 7. 确定性约定

- 目录遍历：每层 `dirs.sort()` + `files.sort()`；catalog/operations/hashes 均按 `source` 稳定排序。
- JSON 序列化：`sort_keys=True` + `ensure_ascii=False` + 2 空格缩进。
- 时间戳只进入 `change_log` 与 `runtime`；`catalog/operations/evidence/versions/failures` 不含墙钟时间，因此同输入重跑逐字节一致。
- `--apply` 幂等：目标已存在且内容哈希一致 → `already_present`，不重复复制、不覆盖。

## 8. 边界与失败映射

| 场景 | 写入 | status |
|---|---|---|
| 输入目录缺失 | `runtime.input_missing=true` | `input_error` |
| 无文件 | `library.catalog=[]` | `insufficient_evidence` |
| 文件不可读/stat 失败 | `failures.permission_errors` + `errors` | `partial` |
| 坏编码/替换字符 | `text_errors` + `degradations` | 视结果 |
| 加密 PDF/无文本层扫描件 | `has_text_layer=false` + `failures.skipped` | `partial` |
| 同名不同版本 | `failures.conflicts` + 后缀日志 | 不误删 |
| 缺 pdftotext/OCR | `degradations` + `has_text_layer=null`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | `partial` |

## 9. 依赖与运行环境

- 仅 Python 3 标准库（≥3.8），无 pip 依赖。
- PDF 文本层由平台 `pdf-text-tool`/`pdftotext`/`ocr-tool` 在 Agent 阶段抽取，脚本只读取标准化的 `/app/work/text/<sha256>.txt`。
- 运行时路径：`/skills/weekly-skill/scripts/main.py`（脚本内部一律用相对 `scripts/`）。
