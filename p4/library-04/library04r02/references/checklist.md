# 六维度覆盖、证据定位与复核清单

本文件是 SKILL.md 第 6 节自检清单的执行细则，供 Agent 与复核人按同一标准落地，避免“引用对不上”的典型失败。

## 1. 六维度覆盖细则（对应“结果完整性”）

| 维度 | 必须产出 | 来源（result.json → library / sources） |
|---|---|---|
| 文件类型 | 按扩展名+内容嗅探归类（text/pdf/archive/image/binary/unknown） | `library.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags，及文件大小、mtime、编码 | `library.metadata[]`、`sources[].metadata` |
| 目录层级 | 相对路径树 + 目录 mtime | `library.directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希的所有路径；空文件单独列出 | `library.duplicate_groups`、`library.summary.empty_files` |
| 版本差异 | 版本基名归族（v1/v2/final/draft/YYYYMMDD 等标记），保留成员与哈希 | `library.version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；来源（task_input / case_json_materials） | `library.permissions_sources[]` |

判定启发式：**内容哈希（SHA256）相同即重复**，不看文件名；空文件单独列出，不并入哈希组。文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时归入同一版本基名；哈希不同说明内容有差异，`version_families` 中两条记录都保留供比对。同一基名、同一哈希、不同路径视为重复副本，不误判为新版本。

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（pdftotext 提取后的页码，页间以 `\f` 分隔）；无文本层时只写 `file+sha256`，标 `unreadable`。
- 引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写。

## 3. 引用核对（finalize 自动执行）

`finalize` 对每条 `evidence` 重新计算文件 SHA256 并在抽取文本/原文件中做逐字子串匹配，产出 `verification`：

- `verified`：`sha_match` 与 `quote_match` 均为真。
- `quote_mismatch`：哈希一致但 quote 未逐字命中（最可能是改写或抄错）。
- `mismatch`：哈希不一致（指向了不同版本/不同文件）。
- `unverifiable`：文件存在但无文本层（PDF 扫描件等），需人工核对。

任何非 `verified` 状态都会写入 `degradations` 并把整体 `status` 降为 `partial`，不隐藏。

## 4. 不确定性分级细则

- `certain`：逐字 quote + 单一来源，且 `verification=verified`。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两个来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：知识库内无依据，写明缺什么（`missing` 字段）。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。

## 5. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认脚本可执行（`python3 scripts/main.py --help` 退出码 0）。
2. 自检：`python3 scripts/selftest.py` 三场景（空目录、坏 JSON、正常样例）应全部通过。
3. 运行：依次执行 `python3 scripts/main.py scaffold → inventory → finalize`（finalize 前由 Agent 写 `/app/work/answers.json`）。
4. 核对：打开 `/app/output/result.json`，确认 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 与 output_contract 全部键齐全且 JSON 合法。
5. 回链：任取一条 `answers[].evidence[]`，按 `locator` 打开原文件，确认 `quote` 逐字命中；`verification.status` 应为 `verified`。
6. 复现：以相同输入重跑，比对 `reproducible_hash`（相同输入两次一致）。

## 6. 完成标准

- 资料结果与输入文件和题目规则一一对应；未对应的字段进 `pending_human` 并说明原因。
- 操作证据（哈希、权限、来源、变更、日志）足以让他人复跑并核对。
- 失败与恢复（跳过、冲突、无权限、重复、恢复）不把未完成写成完成。
