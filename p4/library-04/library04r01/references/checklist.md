# 六维度覆盖与复核清单

本文是 SKILL.md 第 6 节自检清单的执行细则，供 Agent 与复核人按同一标准落地，
避免“回答通顺但引用对不上文件/段落/版本”的典型失败。六维度对应评分项“结果完整性”。

## 1. 六维度覆盖细则

| 维度 | 必须产出 | 来源（inventory.json） |
|---|---|---|
| 文件类型 | 按扩展名 + 内容嗅探归类 text/pdf/office/image/archive/json/empty/binary/unknown | `summary.file_types`、`files[].type` |
| 元数据字段 | Markdown frontmatter 的 title/author/date/tags/version 等，及大小、mtime、编码、行数 | `files[].metadata`、`files[].text_encoding` |
| 目录层级 | 相对路径树，含目录与文件 | `directory_tree` |
| 重复文件 | 按 SHA256 分组，列出同哈希全部路径；空文件单独列 | `duplicate_groups`、`empty_files` |
| 版本差异 | 版本基名分组（v1/v2/final/draft/日期），列出成员与哈希，标注内容是否有差异 | `version_families` |
| 权限与来源 | mode 八进制、可读/可写位、uid/gid；材料来源 task_input 或 case.json 材料列表 | `files[].permissions`、`files[].source` |

## 2. 版本与重复判定启发式

- **重复**：内容哈希（SHA256）相同即为重复，不看文件名；空文件单独列出，不并入哈希组。
- **版本**：文件名含 `v1/v2`、`version-`、`rev-`、`final`、`draft`、`YYYYMMDD` 等标记时
  归入同一版本基名；哈希不同说明内容有差异，`content_differs=true`，需保留两条记录供比对。
- **同一基名 + 同一哈希 + 不同路径**：视为重复副本，不误判为新版本。

## 3. 逐条自检清单（提交/交付前勾选）

### 完整性
- [ ] `library` 覆盖文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六项。
- [ ] `inventory.md` 与 `inventory.json` 中的文件数、字节数一致。

### 正确性
- [ ] 每条 `answers[].evidence[]` 的 `quote` 为原文逐字片段，`locator` 打开后能逐字命中。
- [ ] 版本与权限处置正确：重复副本、内容有差异的版本均被区分。

### 可核验性
- [ ] `evidence_map` 每条 evidence 含 `file+sha256+locator+quote+quote_sha256`。
- [ ] `finalize` 后每条 evidence 的 `verification.status` 均为 `verified`（无法核验者写明 note）。
- [ ] `runtime.deterministic_fingerprint` 存在，可复跑核对。

### 不确定性
- [ ] 证据不足 / 冲突 / 不可读 / 权限受限均分级提示并说明缺什么，写入 `uncertainty` 与 `pending_human`。

### 可复现
- [ ] 同一输入两次运行：文件顺序、哈希、重复组、版本家族、指纹一致（墙钟时间除外）。

### 失败记录
- [ ] 跳过 / 冲突 / 无权限 / 重复 / 恢复均写入 `errors` / `degradations` / `change_log`，
  未完成不写完成（`status` 如实为 `partial/insufficient_evidence/input_error`）。

### 契约回填
- [ ] `result.json` 含 `case_id` 与 `output_contract` 全部字段（缺值给 `null/[]/{}`），JSON 合法。

## 4. 复核人复跑步骤

1. 安装：把包放到 `/skills/weekly-skill`，确认脚本可执行。
2. 自检：`python3 scripts/selftest.py`，退出码应为 0。
3. 运行：依次执行
   `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work` →
   `python3 scripts/main.py inventory ...` →（Agent 写 `/app/work/answers.json`）→
   `python3 scripts/main.py finalize ... --answers /app/work/answers.json`。
4. 核对：打开 `/app/output/result.json`，确认 case_id、status、output_contract、
   sources、uncertainty、pending_human、change_log 齐全且 JSON 合法。
5. 回链：任取 `evidence_map.evidence[]`，按 `locator` 打开原文件，确认 `quote` 逐字命中；
   也可运行 `python3 scripts/main.py verify ...` 复核 `evidence_map.verified.json`。
6. 降级：空目录、坏 JSON、缺 pdftotext 三种情况各跑一次，确认产物合法且退出码不崩溃。

## 5. 降级与边界

- **空输入目录**：`status=insufficient_evidence`（或 case.json 缺失时为 `input_error`），
  退出码 0，写合法结果并列出缺口。
- **坏 JSON / 坏编码**：捕获写入 `errors`/`degradations`，继续处理其余材料。
- **缺 pdftotext / OCR**：PDF 无文本层标 `unreadable/needs_ocr`，不编造文本。
- **单文件 > 2MB**：跳过该文件并记录 `size_skipped`，其余材料继续处理。
- **写盘失败**：降级为最小合法产物（含 case_id/status/sources/uncertainty/change_log），
  不让进程崩溃；运行时产物与最终 JSON 合法性相互独立。
