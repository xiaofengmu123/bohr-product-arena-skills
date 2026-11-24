# 整理规则与字段映射（可复用）

本文件是 `sci-library-organizer` 的规则表，供复核、复用与扩展；脚本 `core_impl.py` 中的常量与本文件一一对应。改词表/模板后重跑脚本即生效，无需改主流程。

## 1. 目录层级（--hierarchy）

| 维度 | 取值来源 | 缺失降级 |
|---|---|---|
| document_type | `detect_file_type`：bibliography / journal_article / conference_paper / preprint / thesis / supplementary / dataset / scan / notes / document / unknown | `unclassified` |
| topic | `TOPIC_VOCAB` 词表命中（见下） | `unclassified` |
| year | BibTeX/RIS/正文中的年份 | `year_not_reported` |
| ext | 扩展名小写 | `noext` |
| source | 源相对路径（sanitize 后） | — |

`category_path(rec, dims)` 按维度顺序拼接路径段；路径段经 `_sanitize_seg` 清洗（非法字符→`_`，≤60 字符）。

## 2. 主题词表（TOPIC_VOCAB，标签归一防爆炸）

粗粒度受控词表（15 域），命中标题/摘要/正文首段/文件名任一关键词即归属，未命中写 `unclassified`，不造标签：

machine_learning / optimization / vision / nlp / reinforcement_learning / bioinformatics / materials / physics / chemistry / statistics / systems / security / robotics / climate / economics

每个主题含中英同义表面形式（如 `deep learning`、`深度学习` 同归 machine_learning），实现同义合并；多命中按词表顺序取列表（`topics`），`topic` 取首个。

## 3. 命名模板（suggest_name）

`FirstAuthor-Year-Title.ext`；作者取 `authors[0]`，标题/作者/年份经 `_sanitize_name` 清洗，总长 ≤120 字符。任一字段缺失则降级为清洗后的原文件名（去扩展名），再缺为 `unnamed`。

同名不同内容（SHA-256 不同）由 `apply_operations` 自动追加 `-2`/`-3` 后缀，**绝不覆盖、绝不删除**。

## 4. 去重与版本关系

- 字节级重复：SHA-256 完全一致 → 后出现者 `duplicate_of` 指向前者，`keep=false`，写 `failures.duplicates`。
- 版本分组：并查集（`_UF`），键为 `doi:<norm>` / `arxiv:<norm>` / `ty:<norm_title>:<year>`；同键或共享键即同组。仅当组内存在**不同哈希**时才成立 `version_group`。
- `version_role`：`supplementary`（文件名含 supplement/suppl/appendix）、`preprint`（有 arXiv 无 DOI）、`published`（有 DOI）、`revision`（文件名含 v\d+/rev），否则 `unknown`。只在有标记时判定。
- 组内多成员不同内容 → 全部保留，`keep` 不受版本分组影响（仅字节重复件降级为不复制）。

## 5. decisions.json 结构（Agent 增量补元数据）

```json
{
  "files": {
    "materials/paper.pdf": {
      "file_type": {"value": "journal_article", "evidence": [{"locator": "materials/paper.pdf#p12", "quote": "doi:10.x/..."}]},
      "topic": {"value": "vision", "evidence": [{"locator": "materials/paper.pdf#p3", "quote": "object detection"}]},
      "metadata": {
        "title": {"value": "A Title", "evidence": [{"locator": "…", "quote": "原文逐字片段"}]},
        "authors": {"value": "Smith, John", "evidence": [{"locator": "…", "quote": "…"}]},
        "year": {"value": "2023", "evidence": [{"locator": "…", "quote": "…"}]}
      }
    }
  }
}
```

- 顶层也接受 `{"<source>": {...}}`（无 `files` 包装）。
- 值必须为 `{value, evidence:[{locator, quote}]}`；`quote` 逐字、≤400 字符。
- 无证据（缺 quote/locator）或值为裸字符串 → 不采纳，写入 `pending_human`。
- 合并后脚本重算 `category_path`/`suggested_name`/`version_role` 并重跑版本分组。

## 6. 字段映射（result.json）

| 题面组 | result 字段 |
|---|---|
| 资料结果 | `library`（catalog/hierarchy/metadata/versions） |
| 操作证据 | `evidence`（hashes/permissions/text/locators）+ `operations` |
| 失败与恢复 | `failures`（skipped/conflicts/permission_errors/duplicates/recoveries） |

六类要素覆盖：文件类型=`catalog[].file_type`；元数据=`catalog[].metadata`；目录层级=`catalog[].category_path`；重复=`failures.duplicates` + `catalog[].duplicate_of`；版本差异=`library.versions` + `catalog[].version_group`/`version_role`；权限与来源=`evidence.permissions` + `evidence.hashes`。

## 7. 元数据来源与保守性

- 元数据只来自原文/BibTeX/RIS/decisions；未提取字段为 `null`/空数组，绝不臆造。
- BibTeX：`title/journal/year/doi/author/keywords`（`_field`）；RIS：`TI/PY/AU/JO/DO`（`_ris_field`）。
- DOI/arXiv/年份可从 PDF 文本强信号抽取（`DOI_RE`/`ARXIV_RE`/`YEAR_RE`），每条带 `locator:line` + 逐字 quote。
- 两处冲突（如 BibTeX 与 PDF 文本 DOI 不一致）→ 两处都记 `evidence`，标 `source_conflict`。

## 8. 失败场景 → 字段映射

| 场景 | 写入 | status 影响 |
|---|---|---|
| 不可读文件 / stat 失败 | `failures.permission_errors` + `errors` | partial |
| 坏编码（回退 latin-1） | 文件条目 `errors` + `degradations` | partial |
| 损坏/加密 PDF / 无文本层扫描件 | `has_text_layer=false` + `failures.skipped` | partial |
| 中文乱码 | `degradations`，utf-8-sig/utf-8/latin-1 顺序重读 | 视结果 |
| 同名不同版本 | `failures.conflicts` + `operations` 后缀日志 | 不误删 |
| 权限不足 | `evidence.permissions` 记 `readable/writable` | partial |
| 缺 pdftotext/OCR | `degradations`，保留清单与哈希 | 不影响清单 |
| decisions 无证据 | `pending_human`，字段不采纳 | partial |

## 9. 幂等与回滚

- 确定性排序：`os.walk` 内 `sorted(fn)`；catalog/versions/failures 均按源路径稳定排序。
- `--apply` 重复运行：目标已存在且 SHA-256 相同 → `already_present`，不重复复制；不同 → `-N` 后缀。
- `rollback.json` 列出本次创建的每个目标文件；删除这些文件即回滚，`/input` 永不被改动。
