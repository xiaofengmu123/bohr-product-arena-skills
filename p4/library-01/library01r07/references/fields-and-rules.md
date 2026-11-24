# 字段字典与规则（fields-and-rules）

## 1. 结果字段字典

`result.json` 顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，非空；从 case.json 或 task.md 识别 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | list | 每条 `{item, locator, quote}`，quote 为逐字原文或计算证据 |
| `uncertainty` | list | 每条 `{item, reason, severity}` |
| `pending_human` | list | 需人工补录/裁决的事项 |
| `change_log` | list | 每条 `{time, actor, step, detail}` |
| `errors` | list | 致命/解析错误字符串 |
| `degradations` | list | 降级处理记录（too_large/unreadable/broken_symlink 等） |
| `runtime` | object | `{started_at, finished_at, seconds, python, platform}` |
| `limitations` | list | 方法边界与已知局限 |
| `computed` | object | 机械证据，见下 |

`computed` 子字段：`summary`、`inventory`（files/dirs/symlinks/others）、
`duplicate_groups`、`version_candidates`、`version_notes`、`aliases`、`catalogs`、
`reconciliation`、`index`、`metadata`、`artifacts`、`contract_key`、`contract_schema`。

## 2. output_contract 回填规则

- 自动识别契约键：`output_contract` / `output` / `result_schema` / `contract` /
  `output_schema` / `response_format` / `expected_output`；允许嵌套于 `task`/`case`/`spec`。
- 每个契约字段**原样回填**到 result.json 顶层，缺值给 `null`（数组/对象给 `[]`/`{}`），
  不改名、不删未知键。
- 若契约字段名与保留键（`case_id`、`status`、`computed` 等）冲突，改存
  `computed._contract_<字段>`，原始契约仍存 `computed.contract_schema` 备查。

## 3. 清单字段（library_manifest.csv）

`path`（相对路径，POSIX 分隔）、`type`（file/dir/symlink/other）、`size`（字节）、
`mtime_iso`（UTC ISO）、`mode`（八进制权限字符串 `0o...`）、`sha256`（内容哈希）、
`status`（ok/too_large/unreadable/non_regular/changed_during_read/symlink/broken_symlink）、
`link_target`（符号链接目标）、`st_nlink`（硬链接计数）。

## 4. 去重与版本策略（均为确定性启发式）

- **去重**：按内容 SHA-256 分组；同一哈希、不同路径 → 重复组。**不按文件名**。
- **版本候选**：先归一化主干（去扩展名、小写、剥离 `v/ver/version/rev N`、日期
  `YYYYMMDD`/`YYYY-MM-DD`、`(N)` 后缀、`copy/final/draft/old/new/backup/副本/终稿`
  等），再按 (归一化主干, 扩展名) 分组；组内**内容哈希 ≥ 2 种**才视为版本候选，
  按 mtime 升序排链，相邻节点记录 `vs_previous`（identical_content / modified_same_size /
  size_changed）。**结果标注 `confidence: heuristic`**，须由 Agent 复核确认。
- 全部内容哈希相同的同名/异名文件归入重复组，不误判为版本。

## 5. 别名识别

- **符号链接**：记录 `link_target`；目标不存在的标 `broken_symlink`。
- **硬链接**：按 `(st_dev, st_ino)` 分组，`st_nlink > 1` 且组内路径 ≥ 2 才记为硬链接组。

## 6. 恢复对账（reconciliation）

对每个库目录（JSON/CSV/BibTeX/RIS/SQLite）解析出的记录，按文件标识（path/filename/
url/id）匹配实际文件，再比对 hash/size，产出：

- `matches`：文件存在且哈希/大小一致；
- `mismatches`：文件存在但哈希/大小不一致（记录 catalog_hash 与 file_hash）；
- `missing`：目录记录无对应文件；
- `orphaned`：文件未被任何目录记录引用。

无目录时 `available=false` 并写明 `no library catalog detected`。

## 7. patch.json（Agent 语义层增量合并）

`--update patch.json --result /app/output/result.json` 深合并补丁到 result.json。
推荐结构：

```json
{
  "status": "ok",
  "computed": {
    "version_candidates": { "<stem+ext>": { "confidence": "confirmed", "note": "…" } },
    "answers": { "<问题>": { "answer": "…", "sources": [{"item":"…","locator":"…","quote":"…"}] } },
    "metadata_overrides": { "<path>": { "author": "…", "year": 2020 } }
  },
  "sources": [ { "item": "…", "locator": "…", "quote": "…" } ],
  "uncertainty": [ { "item": "…", "reason": "…", "severity": "low|medium|high" } ],
  "pending_human": [ "…" ]
}
```

合并后自动追加一条 `change_log`（actor=agent）。

## 8. 确定性约定

- 遍历按路径排序（`dirnames.sort()` + `filenames.sort()`），哈希流式稳定。
- 所有 JSON 输出 `sort_keys=True`，无随机数、无网络、无时间注入进 computed。
- 唯一非确定项为 `runtime`/`change_log` 中的时间戳，其余内容两次运行必须一致。
