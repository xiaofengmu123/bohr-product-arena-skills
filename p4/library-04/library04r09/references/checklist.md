# 六维度规则、证据定位与复核清单

本文件是 SKILL.md 第 6 节自检清单的执行细则，供 Agent 与复核人按同一标准落地。

## 1. 六维度覆盖细则（对应「结果完整性」）

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 按扩展名 + 魔数嗅探归类（text/pdf/office/image/archive/unknown） | `inventory.json → summary.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags，及大小、mtime、编码 | `inventory.json → files[].metadata/text_encoding` |
| 目录层级 | 相对路径树，含目录 mtime | `inventory.json → summary.directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希所有路径；空文件单独列出 | `summary.duplicate_groups` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/日期），保留成员与哈希 | `summary.version_families` |
| 权限与来源 | mode 八进制、可读/可写/可执行位、uid/gid；来源 task_input | `files[].permissions/source` |

## 2. 证据定位规范（locator）

- 文本：`<相对路径>#L<起>-L<止>`，单行写 `#L<n>`。
- PDF：优先 `#p<页码>`（pdftotext 抽取后的页码）；无文本层只写 `file+sha256` 并标 `unreadable`。
- 引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写。
- `finalize` 回读原文件（PDF 用 `/app/work/pdf_pages/<sha256>.json` 缓存）校验 quote 命中，`verified=false` 记入 `failures_recovery`。

## 3. 不确定性分级

- `certain`：逐字 quote + 单一来源。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两个来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：知识库内无依据，写明缺什么。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。

## 4. 版本与重复判定启发式

- 重复：**SHA256 相同**即为重复，不看文件名；空文件不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时归入同一版本基名；哈希不同即内容差异，在 `version_families` 保留全部成员供比对。
- 同一基名、同一哈希、不同路径：视为重复副本，不误判为新版本。

## 5. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认 `python3 scripts/main.py scaffold --help` 退出码 0。
2. 运行：依次 `scaffold → inventory → finalize`（finalize 前写入 `answers.json`）。
3. 核对：打开 `/app/output/result.json`，确认 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 与 output_contract 全部键齐全且 JSON 合法。
4. 回链：任取 `answers[].evidence[]`，按 `locator` 打开原文件，确认 `quote` 逐字命中；`verified=false` 视为错误并修正。
5. 复查：空输入目录、坏 JSON、缺 `pdftotext` 三种情况各跑一次，确认降级产物合法且不崩溃。
6. 自检：`python3 scripts/selftest.py` 应输出 `SELFTEST PASSED`。

## 6. 确定性承诺

脚本对所有目录遍历按相对路径排序、JSON 按 key 排序、哈希固定；`result.json` 中唯一非确定字段是时间戳（`runtime` 与 `change_log[].ts`），去除后同一输入两次运行逐字节一致。
