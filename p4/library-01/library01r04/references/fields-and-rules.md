# 字段字典与判定规则

本文件是 `result.json` 与可编辑产物的字段约定，供 Agent 与评审人复核。

## result.json 保留键

| 键 | 含义 | 缺省值 |
|---|---|---|
| `case_id` | 案例标识（case.json 的 `case_id`/`id`/`challenge_id`） | 空字符串则 `--check` 失败 |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error` | `partial` |
| `sources` | 关键结论证据 `{item, locator, quote}` | `[]` |
| `uncertainty` | 不确定项 `{item, severity, reason}`；severity ∈ high/medium/low | `[]` |
| `pending_human` | 需人工确认事项 | `[]` |
| `change_log` | `{time, actor, step, detail}` 时间线 | `[]` |
| `errors` | 不可恢复错误 | `[]` |
| `degradations` | 已降级处理的非致命问题 | `[]` |
| `runtime` | 引擎版本、起止时间、路径 | 引擎填充 |
| `limitations` | 能力边界 | `[]` |
| `computed` | 机械证据（inventory/summary/duplicate_groups/version_candidates/version_notes/aliases/reconciliation/answers/index） | `{}` |
| `artifacts` | 可编辑产物路径映射 | `{}` |

题目 `output_contract` 的字段**原样回填**到 result 顶层；与上表保留键重名时改存
`computed._contract_<字段>`，不改名、不删未知键。契约缺值给 `null`（对象）/`[]`
（数组）/`""`（字符串），与契约声明类型一致。

## 文件条目（`computed.files` 与 library_manifest.csv）

`path, type, size, mtime, mode, sha256, encoding, link_target, link_broken,
hash_note, read_note, preview`。

- `type` ∈ file / dir / symlink / other / unstatable。
- `hash_note`：`too_large`（>2MB 跳过内容哈希）/`non_regular`/`read_error`/
  `stat_error`。
- `preview`：文本文件前 200 字符；二进制或超限时为空。
- 权限 `mode` 为八进制字符串（如 `644`、`755`）。

## 去重与版本判定规则

- **重复**：仅按内容 SHA-256 分组，`count > 1` 才算重复组；不按文件名。
- **版本候选**：对文件名去扩展名后，剥离子版本标记（`v1.2`/`final`/`draft`/
  `copy`/`副本`/`备份`/`定稿`/`修订`/ISO 日期/8 位日期）得到归一化主干；同一主干下
  至少 2 个成员且至少 2 个不同内容哈希才进入候选链；按修改时间排序，标注
  `oldest`/`newest`。**这是启发式**，写入 `uncertainty` 与
  `version_candidates_note`，需 Agent 按源文本确认谁替代谁。

## 恢复对账

`matches`（引用路径精确命中）/`mismatches`（仅 basename 命中，标注 `basename_only`）/
`missing`（引用在清单中但不在库中）/`orphaned`（库中文件未被任何清单引用）。

## 证据规范

- 每个关键结论给 `locator`（相对路径，可用 `#L行号` 或 `#p页`）与 `quote`（逐字原文）。
- 冲突信息都保留并标 `source_conflict`。
- 无法确认的字段写 `not_reported`；不能伪造哈希、DOI、数值或来源。
