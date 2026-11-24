# 六维度覆盖、证据定位与不确定性分级细则

本文件是 SKILL.md 自检清单的执行细则，供 Agent 与复核人按同一标准落地，避免“引用对不上”。

## 1. 六维度覆盖（对应“结果完整性”）

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 按扩展名 + 内容嗅探归类（text/json/csv/pdf/office/image/archive/binary/unknown） | `inventory.json → summary.file_types` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags/version，以及 size、mtime、encoding | `inventory.json → files[].metadata` |
| 目录层级 | 相对路径树（含目录 mtime、文件计数）与扁平目录索引 | `inventory.json → directory_tree / directories` |
| 重复文件 | 按 SHA256 分组，列出同哈希的所有路径 | `inventory.json → duplicate_groups` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/rev/日期），列出成员、哈希、token | `inventory.json → version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；材料来源（task_input） | `inventory.json → files[].permissions/source` |

以上六项统一在 `inventory.json → summary.six_dimensions` 给出覆盖结论（covered / partial / none_found / not_applicable）。

## 2. 证据定位（locator）规范

- 文本：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（pdftotext 提取后的页号）；无文本层只写 `file+sha256`，标 `unreadable`。
- 每条证据必须同时给 `file`（相对路径）、`sha256`、`locator`、`quote`（原文逐字片段）。
- 禁止改写 quote；`finalize` 会回读原文件做归一化子串核对，`verified: true/false/null` 如实记录。

## 3. 不确定性分级

- `certain`：逐字 quote + 单一来源且 `verified=true`。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两个来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：库内无依据，写明缺什么，`missing` 非空。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。

第 4 级缺证据必须以 `insufficient_evidence` 诚实作答，而不是编造结论。

## 4. 版本与重复判定启发式

- 重复：**SHA256 相同即为重复**，不看文件名；空文件单独列出，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`8 位日期` 等 token 时归入同一版本基名；哈希不同说明内容有差异，需在 `version_families` 保留多条记录。
- 同基名 + 同哈希 + 不同路径：视为重复副本，不误判为新版本。

## 5. 复核人复跑步骤

1. 把包放到 `/skills/weekly-skill`，确认三个脚本都可执行：
   `python3 scripts/main.py --help`、`python3 scripts/inventory.py --help`、`python3 scripts/selftest.py --help` 均退出码 0。
2. 运行：`main.py run`（等价于 scaffold → inventory → finalize；finalize 前写入 `work/answers.json`）。
3. 复核：打开 `/app/output/result.json`，确认 `case_id`、`status`、`sources`、`uncertainty`、`change_log`、`pending_human` 与 output_contract 全部键齐全、JSON 合法、`contract_fidelity.preserved_keys == total_keys`。
4. 回链：任取一条 `answers[].evidence[]`，按 `locator` 打开原文件，确认 `quote` 逐字命中，且 `verified=true`；对的记为错误并修正。
5. 自检：`python3 scripts/selftest.py` 跑空输入、坏 JSON、缺 case.json 三种降级，确认退出 0 且产物合法。