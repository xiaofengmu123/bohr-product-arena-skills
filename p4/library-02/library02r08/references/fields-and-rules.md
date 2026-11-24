# 字段与规则（fields-and-rules）

本文件说明脚本的确定性字段语义、判定规则与证据定位规范，供他人复跑与复核。

## 1. result.json 字段语义

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从 case.json 的 `case_id`/`id`/`challenge_id` 或目录名识别，非空 |
| `status` | string | `ok`/`partial`/`insufficient_evidence`/`input_error` |
| `sources` | list | 每条 `{item, locator, quote}`，关键结论的证据定位 |
| `uncertainty` | list | 每条 `{item, reason, severity}` |
| `pending_human` | list | 需人工确认项 |
| `change_log` | list | 每条 `{time, actor, step, detail}` |
| `errors` / `degradations` | list | 异常与降级，绝不静默跳过 |
| `runtime` | object | `{seconds, now}` |
| `computed` | object | 机械证据：files/summary/duplicate_groups/version_candidates/version_notes/aliases/reconciliation/answers/filters/artifacts |

`output_contract` 的全部字段在 result.json 顶层**原样回填**：缺值给 `null` 或 `[]`，
不改名、不删未知键；与保留键（如 `status`/`sources`）冲突时，改存
`computed._contract_<字段>`。

## 2. 文件记录字段（library_manifest.csv / computed.files）

`path`（POSIX 相对路径）、`name`、`ext`、`size`、`mtime`（ISO 8601 UTC）、`mode`
（八进制权限）、`is_symlink`、`symlink_target`、`symlink_resolves`、`inode`、`nlink`、
`is_text`、`sha256`、`hash_status`。

`hash_status` 取值与含义：`ok`（已哈希）、`too_large`（>2MB，仅元数据）、
`unreadable`、`non_regular`、`symlink`。内容哈希用 SHA-256。

## 3. 版本候选规则（启发式）

1. 归一化主干：去扩展名、转小写、统一分隔符、抹掉 `v1/final/draft/rev/副本/修订/
   终稿/初稿/扫描件/scan/copy/bak` 等版本与副本标记、抹掉版本号。
2. 同主干聚类；簇内按版本号 + 名称排序。
3. 成对相似度：文本文件用 `difflib.SequenceMatcher` 比较正文前 20K 字符，否则比较
   归一化名称。相似度阈值不硬切，全部输出并标 `heuristic=true`。
4. 版本候选**不自动判定同一作品**，需 Agent/人确认后写进 `change_log`。

## 4. 别名规则

- 符号链接：`lstat` 识别，`os.readlink` 取目标，`os.path.exists` 判定断链。
- 硬链接：`nlink > 1` 且 `is_regular` 且非符号链接，按 `inode` 分组。

## 5. 恢复对账规则

- 清单来源：名称含 `manifest/library/catalog/index/inventory/清单/目录/索引` 的
  JSON/CSV/Markdown 文本文件。
- `matches`：清单与磁盘都在，哈希一致或清单未给哈希；`mismatches`：哈希不一致；
  `missing`：清单登记但磁盘无；`orphaned`：磁盘有但清单未登记（排除符号链接）。

## 6. 证据定位（locator）规范

- 文本/检索命中：`相对路径#L行号`（段落起始行；PDF 为 pdftotext 提取文本的行号）。
- 印刷页码与 PDF 物理页错位、双栏、跨页表格与图注：脚本只给出提取文本行号定位，
  由 Agent 用 pdf-render-tool/OCR 补页码并在 `uncertainty` 说明，不虚构页码。
- 目录树：`[空目录] 相对路径/`。
- `quote` 必须为原文逐字片段；哈希类结论的 quote 写哈希值本身。

## 7. 过滤硬约束

年份/项目/类型/批次/版本/权限各自独立校验，任一不满足即排除该文件并记入
`computed.filters.excluded`；全部被排除则结果如实为空并在 `uncertainty` 报
`severity=high`，不放宽条件。

## 8. 确定性保证

目录遍历（`os.walk` 的 dirnames/filenames 排序）、哈希稳定、无随机数、无时间相关
排序；唯一例外是 `runtime` 与 `change_log` 中的时间戳。`selftest.py` 第 7 项对此
做回归验证。
