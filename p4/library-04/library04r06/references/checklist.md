# 六维度覆盖、证据定位与复核清单

本文件是 SKILL.md 自检清单的可执行细则，供 Agent 与复核人按同一标准落地，避免
“回答通顺但引用对不上文件/段落/版本”的典型失败。所有定位均来自
`/app/work` 下中间表示与 `/app/output` 下最终产物。

## 1. 六维度覆盖细则（对应“结果完整性”）

`inventory.json → library` 是唯一事实来源，`finalize` 将其整段回填进
`result.json → library`。六项缺一不可：

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 扩展名 + 内容嗅探归类（text/pdf/office/image/archive/unknown），按类型计数并列出文件 | `library.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags 等，及 size_bytes、mtime、text_encoding、sha256 | `library.metadata_fields` |
| 目录层级 | 相对路径树（`__files__` 列出叶子文件，稳定排序） | `library.directory_tree` |
| 重复文件 | 按 SHA256 分组，列同哈希全部路径；空文件单列 | `library.duplicate_files` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/YYYYMMDD 等），列出成员、marker、各哈希；同基同哈希视为副本 | `library.version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；来源 task_input 或 material | `library.permissions_and_source` |

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（页码来自 `evidence_index.json → files[].pages[]`，
  由 pdftotext 换页符拆分）；无文本层时只写 `file + sha256`，标 `needs_ocr`。
- 每条证据必须同时给出 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字
  片段，禁止改写、禁止省略关键限定词。文件改名不影响回链，因为回链以 sha256 为准。

## 3. 不确定性分级细则

- `certain`：逐字 quote + 单一来源（同一内容哈希）。
- `likely`：由原文推断或摘要，非逐字。
- `conflict` / `source_conflict`：同一指标两个来源不一致，全部记录，不隐藏。
- `insufficient_evidence`：库内找不到依据，写清缺什么（`missing` 字段）。
- `unreadable` / `needs_ocr`：文件不可读、扫描件无文本层、权限不足；`degradations`
  同步记录并纳入 `pending_human`。
- `pending_human`：材料缺失/冲突/不可读，需人工介入的条目汇总。

## 4. 版本与重复判定启发式

- 重复：**内容 SHA256 相同**即为重复，不看文件名；空文件（size=0）单列，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-N`、`rev-X`、`final`、`draft`、`YYYYMMDD`、
  `YYYY-MM-DD` 等标记时归入同一版本基名；哈希不同说明内容有差异，保留两条记录供比对。
- 同一基名且同一哈希但路径不同：判定为重复副本，不误判为新版本（`is_duplicate_only`）。

## 5. 复核人复跑步骤

1. **安装**：把包放到 `/skills/weekly-skill`，确认 `scripts/*.py` 可在
   `python3 -B scripts/main.py --help` 退出码 0。
2. **自检**：`python3 -B scripts/selftest.py` 应打印 `ALL PASS` 且退出码 0；
   覆盖空输入、坏 JSON、契约回填、确定性四类失败模式。
3. **运行**：`scaffold → inventory → finalize`（finalize 前写入 `answers.json`）。
4. **核对 JSON**：`/app/output/result.json` 须含 `case_id`、`status`、`sources`、
   `uncertainty`、`pending_human`、`change_log` 与 `output_contract` 全部键，且 JSON 合法。
5. **回链核对**：任取一条 `answers[].evidence[]`，按 `locator` 打开原文件，确认
   `quote` 逐字命中；按 `sha256` 复核内容未变。对不上的记录记错并修正，不写成完成。
6. **确定性**：同一输入在干净 output/work 下跑两次，去掉 `runtime` 与
   `change_log[].time` 后，`result.json` 内容应完全一致。