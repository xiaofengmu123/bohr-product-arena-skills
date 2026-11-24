# 六维度覆盖、证据定位、鲁棒性降级与复核清单

本文件是 SKILL.md 第 6 节自检清单的执行细则，用于让 Agent 与复核人按同一标准落地，避免「引用对不上」的典型失败。

## 1. 六维度覆盖细则（对应「结果完整性」）

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 按扩展名 + 内容嗅探归类（text/pdf/office/image/archive/unknown） | `inventory.json → summary.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags/version，及文件大小、mtime、编码、行数 | `inventory.json → files[].metadata/text_encoding/line_count` |
| 目录层级 | 相对路径树，含每层目录与文件、文件 SHA256 | `inventory.json → directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希的所有路径；空文件单独列出 | `inventory.json → duplicate_groups / empty_files` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/日期），列出成员、各自哈希与版本标记 | `inventory.json → version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；材料来源（task_input 或 case.json 材料列表） | `inventory.json → files[].permissions/source` |

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（用 pdftotext 提取后的页号）；无文本层时只写 `file + sha256`，标 `unreadable`/`needs_ocr`。
- 引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写。
- `finalize` 自动校验四件事并把结果写入 `evidence[]._verified`：
  - 文件是否存在（`file-missing`）
  - SHA256 是否一致（`hash-mismatch`）
  - locator 行号/页码是否命中（`locator-ok` / `locator-out-of-range` / `page-ok` / `page-out-of-range`）
  - quote 是否逐字出现（`quote-ok` / `quote-mismatch`）
  - 综合结论：`verified` / `verified-quote-only` / `partially-verified` / `unverified` / `invalid`

## 3. 不确定性分级细则

- `certain`：逐字 quote + 单一来源。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两个来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：知识库内无依据，写明缺什么。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。
- `uncertainty` 中属于 `insufficient_evidence`/`unreadable` 的条目同时提升到 `pending_human`，提示需人工复核。

## 4. 版本与重复判定启发式

- 重复：**内容哈希（SHA256）相同**即为重复，不看文件名；空文件单独列出，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`r2`、`final`、`draft`、`YYYYMMDD`/`YYYY-MM-DD` 等标记时，去掉标记归入同一版本基名；哈希不同说明内容有差异，在 `version_families` 中保留各成员记录供比对。
- 同一基名、同一哈希、不同路径：视为重复副本，不误判为新版本。

## 5. 鲁棒性降级矩阵（本版重点）

| 场景 | 要求 | 期望 status |
|---|---|---|
| 空输入目录（无 case.json/task.md/材料） | 退出码 0，写合法 result.json，列出缺口 | `insufficient_evidence` |
| 坏 JSON（截断/语法错） | 捕获写入 `errors`，尽力恢复部分对象，继续处理其余材料 | `input_error`（若无法取 case_id）或 `partial` |
| 坏编码文件 | 按 utf-8-sig→utf-8→gb18030→cp1252→latin-1 尝试，最后 replace 兜底，写 `degradations` | 视情况 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`，自动建目录 | 不崩溃 |
| 超大文件（>2MB） | 仍算哈希、记元数据，跳过全文解析/逐字校验，写 `degradations` | 不崩溃 |
| 缺 pdftotext | 对应 PDF 标 `unreadable`/`needs_ocr`，写 `degradations`，不编造文本 | 不崩溃 |
| 写盘失败 | 降级为最小合法产物，写 `errors`，不崩溃 | `partial` |
| 超时/中断 | 先落盘部分结果，保留已写结果，`KeyboardInterrupt` 退出码 0 | `partial` |

## 6. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认 `scripts/*.py` 可执行（`python3 scripts/main.py --help` 退出码 0）。
2. 自检：运行 `python3 scripts/selftest.py`，7 个鲁棒性场景应全部 PASS（空输入、坏 JSON、坏编码、缺参数、超大文件、无文本层 PDF、正常案例）。
3. 运行：对真实输入执行 `python3 scripts/main.py full --input /input --output /app/output --work /app/work`（Agent 流程则是 scaffold → 写 answers → finalize）。
4. 核对：打开 `/app/output/result.json`，确认 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与 `output_contract` 全部键齐全且 JSON 合法。
5. 回链：任取一条 `answers[].evidence[]`，看 `_verified` 是否为 `verified`/`verified-quote-only`；按 `locator` 打开原文件，确认 `quote` 逐字命中。对不上的记录应视为错误并修正，不得静默放过。
6. 复查：空输入目录、坏 JSON、缺 pdftotext 三种情况各跑一次，确认降级产物合法且不崩溃（可直接用 selftest 覆盖）。
