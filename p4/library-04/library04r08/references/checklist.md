# 六维度覆盖、证据定位、降级矩阵与复核步骤

本文件是 SKILL.md 第 6、7 节的执行细则，供 Agent 与复核人按同一标准落地，
避免「引用对不上」与「未完成写成完成」的典型失败。

## 1. 六维度覆盖细则（对应「结果完整性」）

| 维度 | 必须产出 | 来源 |
|---|---|---|
| 文件类型 | 按扩展名+内容嗅探归类（text/pdf/office/image/archive/unknown） | `inventory.json → summary.file_types` |
| 元数据字段 | 文本文件的 encoding/char_count/BOM，及文件大小、mtime；Office/PDF 不给无依据的元数据 | `inventory.json → files[].metadata` |
| 目录层级 | 相对路径树，含目录 mtime 与子目录/文件排序 | `inventory.json → directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希的所有路径；空文件单独列 | `inventory.json → duplicate_groups` / `empty_files` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/日期），列出成员与哈希 | `inventory.json → version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；材料来源（task_input 或 materials） | `inventory.json → files[].permissions/source` |

## 2. 证据定位规范（locator）

- 文本文件：`<相对路径>#L<起>-L<止>`，如 `materials/paper.md#L12-L15`；单行写 `#L12`。
- PDF：优先 `#p<页码>`（用 pdftotext 提取后的页号）；无文本层时只写 `file+sha256`，标 `unreadable`。
- 引用必须同时给 `file`、`sha256`、`locator`、`quote`；`quote` 为原文逐字片段，禁止改写。
- 每条证据的 `confidence` 取值与不确定性分级一致（见下节）。

## 3. 不确定性分级细则

- `certain`：逐字 quote + 单一来源。
- `likely`：由原文推断或摘要，非逐字。
- `conflict`：同一指标两个来源不一致，全部记录并标 `source_conflict`。
- `insufficient_evidence`：知识库内无依据，写明缺什么。
- `unreadable`：文件不可读 / 扫描件无文本层 / 权限不足。

同一问题允许出现多条不同分级的证据；冲突不隐藏，缺证不补造。

## 4. 版本与重复判定启发式

- 重复：**内容哈希（SHA256）相同**即为重复，不看文件名；空文件单独列出，不并入哈希组。
- 版本：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时归入同一版本基名；哈希不同说明内容有差异，需在 `version_families` 中保留多条记录供比对。
- 同一基名、同一哈希、不同路径：视为重复副本，不误判为新版本。

## 5. 降级矩阵

| 场景 | 判定 | 处置 | 结果 |
|---|---|---|---|
| 空输入目录 | `case.json` 与 `task.md` 均缺失 | 退出码 0，写合法结果并列出缺口 | `status=insufficient_evidence` |
| 坏 JSON | `case.json` 解析失败 | 记录到 `errors`，继续盘点其余材料 | `status=partial` |
| 坏编码 | 文本非 UTF-8 | latin-1 兜底并记录 `metadata.note` | 继续处理，不崩溃 |
| 缺 `pdftotext` | `shutil.which` 未找到 | PDF 文本层不探测，写 `pdf_text_layer=null` | `degradations` 记录 |
| 扫描件无文本层 | 首屏 `pdftotext` 输出为空 | 标 `unreadable`/`needs_ocr`，不编造文本 | `uncertainty` 记录 |
| 权限不足 | stat/read 抛 OSError | 记录 `errors`，字段给 null | 继续处理，不崩溃 |
| 单文件超 2MB | 大小超上限 | 哈希+元数据照常，不读全文 | `metadata.truncated=true` |
| 写盘失败 | open/write 抛 OSError | 降级为最小合法产物 | `status=partial` |

## 6. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认 `python3 scripts/main.py --help` 退出码 0。
2. 运行：依次执行 `scaffold → inventory → finalize`（finalize 前写入 `answers.json`），再跑 `python3 scripts/selftest.py`。
3. 核对：打开 `/app/output/result.json`，确认 `case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log` 与 output_contract 全部键齐全且 JSON 合法。
4. 回链：任取一条 `answers[].evidence[]`，按 `locator` 打开原文件，确认 `quote` 逐字命中；对不上的记录应视为错误并修正。
5. 复查：空输入目录、坏 JSON、缺 `pdftotext` 三种情况各跑一次，确认降级产物合法且不崩溃（`selftest.py` 已内置这些场景）。
6. 一致性：同一输入连跑两次，比对 `result.json` 的 `sources`/`library` 是否一致（确定性排序保证）。
