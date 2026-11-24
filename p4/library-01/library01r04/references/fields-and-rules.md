# 字段字典与判定规则

本文件是 `scripts/core_impl.py` 产出字段的可复核定义。所有值均来自真实输入文件；无法获取时写 `null` 或 `not_reported`，绝不补造。

## 1. 文件清单字段（library_manifest.csv / computed.files）

| 字段 | 含义 | 缺值规则 |
|---|---|---|
| `path` | 相对 input 根目录的 POSIX 路径 | 必有 |
| `type` | `file`/`dir`/`symlink`/`non_regular` | 必有 |
| `size` | 字节数（regular file） | 无法获取为 `null` |
| `mtime` | ISO-8601 UTC 修改时间 | 无法获取为 `null` |
| `mode` | 权限位八进制字符串（`0755`） | 无法获取为 `null` |
| `sha256` | 内容 SHA-256 十六进制 | 跳过时 `null` |
| `hash_status` | `ok`/`too_large`/`unreadable`/`non_regular` | 必有 |
| `extension` | 小写扩展名（无扩展为 `null`） | - |
| `symlink_target` | 链接目标（仅符号链接） | 非链接为 `null` |
| `symlink_broken` | 目标是否无法解析 | 非链接为 `null` |
| `hardlink_group` | 相同 (st_dev, st_ino) 的组号，>1 表示硬链接 | 无硬链接为 `null` |

## 2. 去重判定（computed.duplicate_groups）

- 依据：**完整文件内容 SHA-256**，不是文件名。
- 组内记录 `sha256`、`count`、`files`（按路径排序）。
- 边界：`hash_status != ok` 的文件不参与分组，此边界写入 `uncertainty`。

## 3. 版本候选判定（computed.version_candidates，启发式）

- 主干归一化：文件名 stem 转小写，删除日期（`YYYY-MM-DD`/`YYYYMMDD`）、版本 token（`v1`、`rev2`、`version 3`、`final`、`draft` 等），折叠分隔符。
- 分组键：`(normalized_stem, extension)`，组内按 `mtime` 升序。
- 差异度量：相邻文件若为文本（≤512KB），给 `SequenceMatcher` 相似度；否则 `null`。
- **全部标记 `heuristic=true`**；只有行政版本说明或哈希/内容证据支撑时，才由 Agent 升级为事实。

## 4. 别名判定（computed.aliases）

- 符号链接：`os.path.islink` + `os.readlink`；目标按链接所在目录解析，解析失败记 `broken=true`。
- 硬链接组：`st_nlink > 1` 且 `st_ino != 0` 时按 `(st_dev, st_ino)` 分组；平台不支持时为空并视为降级。

## 5. 库目录解析（recovery_report.json / computed.reconciliation）

- 支持格式：JSON、CSV、BibTeX、RIS、SQLite。
- 从每条记录抽取 `path`/`hash`/`version`/`author`/`year`/`title`（多别名键自动识别）。
- 对账类别：`matches`（路径或哈希命中）、`mismatches`（路径在但哈希不同）、`missing`（预期但未找到）、`orphaned`（实际存在但目录未引用）。
- 哈希比较大小写不敏感；缺哈希时只按路径判 `match`。

## 6. 版本说明扫描（computed.version_notes）

- 扫描非二进制文本文件（≤1MB/文件）中命中 `replaces`/`supersedes`/`replaced by`/`previous version`/`→`/`->` 等词的行，记录 `file`、`line`、`text`（截断 500 字符）。
- 这只是候选证据，语义确认由 Agent 完成。

## 7. 结果状态语义

| status | 含义 |
|---|---|
| `ok` | 全部契约字段已填、关键结论有证据、无未说明的失败 |
| `partial` | 有可交付结果，但存在降级、不确定或待人工确认项 |
| `insufficient_evidence` | 输入材料不足，无法形成结论（如空目录） |
| `input_error` | 缺 case.json 或输入无法解析 |

## 8. 证据条目格式

```json
{ "item": "duplicate_group", "locator": "materials/a.txt; materials/b.txt", "quote": "sha256=abc123..." }
```

`quote` 必须是输入材料中的逐字片段或脚本生成的机械事实（哈希值、行文本），不允许改写。
