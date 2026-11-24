# 字段字典与判定规则（fields-and-rules）

本文件是脚本实现与人工复核共享的单一事实源。脚本与本文不一致时以脚本实际输出为准，
并在 `uncertainty` 中说明。

## 1. result.json 保留字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 案例标识，优先取 case.json 的 `case_id`/`id`/`challenge_id`/`caseId`，其次从 task.md 匹配 challenge id |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，诚实标注 |
| `sources` | list | 关键结论证据：`{item, locator, quote}`，quote 为逐字片段 |
| `uncertainty` | list | 不确定项：`{item, reason, severity}` |
| `change_log` | list | 操作时间线：`{time, actor, step, detail}` |
| `errors` / `degradations` | list | 处理失败 / 降级记录，绝不静默 |
| `runtime` | object | 引擎版本、时间戳、输入输出路径、大小上限；`generated_at` 为易变字段 |
| `limitations` / `pending_human` | list | 边界限制 / 待人工确认项 |
| `computed` | object | 机械证据汇总：`files`/`summary`/`inventory`/`duplicate_groups`/`version_candidates`/`version_notes`/`aliases`/`reconciliation`/`answers`/`index`/`artifacts` |
| `artifacts` | object | 可编辑产物名 → 绝对路径 |

## 2. output_contract 回填规则

- 在 case.json / task.md 中自动识别键名 `output_contract` / `output` / `result_schema` / `contract`；
  识别不到时从 task.md 的 fenced ```json 块尝试解析。
- 契约的**全部字段原样回填**到 result.json 顶层：缺值给 `null` / `[]`，不改名、不删未知键。
- 契约字段与保留字段重名时，改存 `computed._contract_<字段>`，不覆盖保留字段。
- 契约缺失：result.json 仍合法，`uncertainty` 标 `output_contract` 缺失，`status` 至少为 `partial`。

## 3. 文件清单（library_manifest.csv）字段

`path`（相对 /input 的正斜杠路径）、`type`（file/directory/symlink/non_regular）、
`role`（pdf/markdown/text/csv/json/…/control）、`size_bytes`、`mtime`、`mode`（八进制权限）、
`sha256`、`status`、`note`、`link_target`。目录项无哈希与大小。

## 4. 内容哈希与去重

- SHA-256 流式读取，单文件上限 `MAX_CONTENT_BYTES=2MB`；超限跳过哈希并记 `too_large`，路径/大小/权限/链接目标仍保留。
- 去重按 `sha256` 分组，组内路径排序；同名不同内容**不**视为重复。
- 重复组写入 `duplicate_groups`（含 sha256 前缀、路径列表、count）与 `dedup_version.json`。

## 5. 版本候选（启发式，非真值）

- 归一化主干：去扩展名、小写；匹配 `name_v1.2` / `name-rev3` 等模式后取主干。
- 同主干 ≥2 个文件 → 候选版本链，按路径排序并标注 `method=normalised_stem_heuristic`。
- 版本链是候选而非定论，与行政版本说明（version_notes）对齐后由 Agent 在语义层确认。

## 6. 别名识别

- 符号链接：`os.readlink` 取目标，目标不存在记为 `broken_symlink`。
- 硬链接：按 `(st_ino)` 分组，`st_nlink>1` 的路径聚成硬链接组。
- 全部写入 `aliases`，断链计入 `answers.broken_symlink_count`。

## 7. 库目录解析与恢复对账

- 支持 JSON（dict 或 `{"items":[...]}` 列表）、CSV（DictReader，退化到裸行）、BibTeX（@entry 的 key 与 fields）、RIS（TY/ER 分段）、SQLite（只读 `sqlite_master` 表清单，逐表最多 500 行）。
- 对账以条目里的 `path`/`file`/`filename`/`name` 与实际文件相对路径匹配；`sha256`/`hash` 字段用于 mismatch 判定。
- 输出四类：`matches`（路径匹配且哈希一致或未给哈希）、`mismatches`（哈希不符）、`missing`（清单有而实际无）、`orphaned`（实际有而清单无，排除 control 文件）。
- SQLite 打不开 / 坏表记 `sqlite_open_error` / `sqlite_query_error`，不崩溃。

## 8. 确定性规则（本变体重点）

- 所有遍历用排序后的 `dirnames`/`filenames`；JSON 用 `sort_keys=True`；无随机数。
- 相对路径统一为正斜杠；`answers`/`index` 的列表与字典键均排序。
- 唯一易变字段是 `generated_at` 与 `change_log`/`operation_log` 的 `time`，由 `selftest.py` 规范化后比较。
- 目录树文本由排序后的路径稳定生成，深度上限 `MAX_TREE_DEPTH=12`，空目录标 `[空目录]`。

## 9. 失败与降级

- 空输入 → `insufficient_evidence`；缺 case.json / 坏 JSON → `input_error`；两者都退出码 0，不抛异常。
- 文件不可读 / 坏编码 / 超大 / 非常规 / 符号链接：跳过内容哈希但保留可观察元数据，逐条记录。
- 写盘失败：先落骨架，退出码 2；禁止在错误后留下空产物。
- 缺失信息一律 `not_reported` / `pending_human`，禁止编造哈希、DOI、数值或文件内容。
