# 规则与规范（sci-library-curator）

本文件是可复用规则说明，脚本只做确定性实现；语义判断（读 PDF 文本、判断主题、区分
版本角色）由调用方 Agent 依据原文片段写入 `decisions.json`，脚本只在有证据时采纳。

## 1. 目录层级规范

`--hierarchy` 逗号分隔维度，常见组合：

| 维度 | 取值来源 | 缺失降级 |
|---|---|---|
| `document_type` | 文件名强信号 / BibTeX entry_type / decisions（有证据） | `unclassified` |
| `topic` | 文件名主题词表 / decisions | `uncategorized` |
| `year` | BibTeX / RIS / decisions / 文件名四位年份 | `nodate` |

分类只在强信号或证据成立时判定，弱信号一律不猜：没有依据时 `document_type` 与
`topic` 保持 `null`/`uncategorized`，进入 `uncertainty` 而不是补造类别。

## 2. 命名规则与降级

模板 `author-year-title`（默认）：

1. 有作者姓 + 年份 + 标题 → `Surname_YYYY_Title.ext`
2. 有作者姓 + 年份 → `Surname_YYYY.ext`
3. 有标题 → `Title.ext`
4. 有 DOI → `DOI_<doi>.ext`（或模板 `doi` 直接采用）
5. 都缺失 → 原文件名 slug 化，仍无则 `unknown`

特殊字符经 `slugify` 归一（NFKC、非法字符转下划线、去多余空白、截断到上限），
不改变原文件；同一目标名不同内容用 `-2/-3` 后缀解决，绝不覆盖。

## 3. 标签归一

- 标签只来自：文件名主题词表命中、BibTeX/RIS 关键词、decisions 的 `tags`（带证据）。
- 同义合并：主题词表把 `machine-learning/deep-learning/neural` 归为 `ml`，
  `genom/genetic/gene/dna` 归为 `genomics`，避免标签爆炸。
- 控制粒度：只保留对象/方法/材料级标签，不做上下位自动展开；一个文件多标签按集合
  去重后排序输出。

## 4. 去重与版本区分

- 字节级重复：流式 SHA-256（1 MiB 块）一致 → 后者 `duplicate_of=前者`，`keep=false`。
- 版本组：并查集按 `DOI` / `arXiv` / 归一化`标题+年份` 聚合，只聚合强信号；
  `preprint` / `published` / `supplementary` / `revision` 只在有原文标记时判定，
  否则 `version_role=unknown`。
- 归档而非删除：`--apply` 只复制到 `/app/output/library/`，`/input` 原样不动；
  `rollback.json` 列出本次创建的全部目标文件，删除即可回滚。

## 5. 元数据字段映射

BibTeX / RIS → 内部字段：

| 源字段 | 内部字段 |
|---|---|
| `title` | `title` |
| `author` / `AU` | `authors`（列表） |
| `journal` / `JO/JF/T2` | `journal` |
| `year` / `PY` | `year` |
| `doi` / `DO` | `doi` |
| `eprint` | `arxiv_id` |

BibTeX `entry_type` → `document_type`：`article→journal_article`、
`inproceedings→conference_paper`、`phdthesis/mastersthesis→thesis`、
`book→book`、`incollection→book_chapter`、`techreport→report`、`misc→misc`。

## 6. decisions.json 结构（可选，供 Agent 注入语义判断）

```json
{
  "files": {
    "相对路径/paper.pdf": {
      "document_type": "journal_article",
      "topic": "neuroscience",
      "tags": ["fmri", "cortex"],
      "metadata": {"title": "...", "year": 2020},
      "has_text_layer": true,
      "evidence": [
        {"field": "title", "locator": "text/paper.pdf#p1", "quote": "逐字片段"}
      ]
    }
  }
}
```

- 每个字段都必须有 `evidence`（或 `locator`+`quote`）；无证据字段写入
  `pending_human`，脚本不采纳。
- 证据冲突时两处都记录并标 `source_conflict`，不隐藏。

## 7. 依赖与边界

- 仅 Python 3 标准库（≥3.8），无需 pip；跨平台用 `/` 分隔相对路径，保证确定性。
- `pdftotext` 可用时探测 PDF 文本层；不可用则如实写入 `degradations`，不影响清单。
- 不解决：单篇精读、全文翻译、写作润色、文献检索发现。
- 不生成预写答案、不宣称基准分数。
