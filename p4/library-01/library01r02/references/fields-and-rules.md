# 字段字典与处理规则

本文件是 `scripts/core_impl.py` 的配套说明，供 Agent 复核语义结论、二次补录与撰写 `sources`/`uncertainty` 时使用。所有脚本字段、判定规则与降级码以本文件为准；冲突时以真实源文件内容为准。

## 1. 结果主字段（result.json）

| 字段 | 类型 | 含义与填写规则 |
|---|---|---|
| `case_id` | string | 从 case.json 的 `case_id`/`id`/`challenge_id` 或 task.md 提取；不得为空 |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | list | 每个关键结论一条：`{item, locator, quote}`，quote 必须逐字 |
| `uncertainty` | list | 不确定项：`{item, reason, severity}`，severity 用 `high/medium/low` |
| `pending_human` | list | 需要人工补录或裁决的事项 |
| `change_log` | list | `{time, actor, step, detail}`，append-only 时间线 |
| `errors` / `degradations` | list | 逐条异常；不能静默跳过 |
| `runtime` | object | 预算、阶段耗时、时间戳；机械证据不得依赖其数值 |
| `computed` | object | 全部机械证据（见下） |

## 2. computed 子结构

- `files`：逐文件记录 `path/type/size/mtime/mode/sha256/md5/hash_status/hash_note`。
- `summary`：`counts`（files/dirs/entries/total_bytes/symlinks/broken_symlinks/hash_errors）与 `extensions` 计数。
- `inventory`：`entries`（含目录）、`dirs`。
- `duplicate_groups`：按内容 SHA-256 分组，`n>1` 才入组，`paths` 升序。
- `version_candidates`：按"归一化主干 + 扩展名"分组，`n>1` 才入组；每组按 mtime 升序给 `order/label`，`heuristic=true`。
- `version_notes`：从版本说明类文本文件解析的 `vA -> vB` 转移记录，含逐字 `quote` 与行号。
- `aliases`：`symlinks`、`broken_symlinks`、`hardlink_groups`（按 `(st_dev, st_ino)` 聚合）。
- `catalogs`：每个库目录文件的解析结果与错误。
- `reconciliation`：`matches/mismatches/missing/orphaned` 四类及计数。
- `answers`：机械问答（计数、重复、断链、版本、按文件名抽取的年份/作者，标注启发式）。
- `index`：可检索索引，`path/name/ext/size/mtime/sha256/keywords`。

## 3. 判定规则

### 3.1 去重

- 判定键 = 内容 `sha256`，绝不按文件名。
- 文件大小 > 2MB（`HASH_CAP_BYTES`）跳过内容哈希，`hash_status=too_large`，不计入重复组。
- 不可读/非常规文件保留路径、大小、权限与链接目标，记录 `unreadable`/`non_regular`。

### 3.2 版本候选

1. 去掉扩展名后做小写归一化：去除 `v1/v1.0/rev/draft/final/copy/副本/修改/备份` 等标记、括号与分隔符，得到语义主干。
2. 按 `(语义主干, 扩展名)` 分组，成员 ≥2 才生成候选链。
3. 组内按 mtime 升序标 `v1..vn`；`unique_content_hashes` 表示实际内容差异数。
4. **一律标 `heuristic=true`**；最终"谁替代谁"由 Agent 结合版本说明与内容 diff 确认后写入 patch。

### 3.3 别名

- 符号链接：`os.lstat` 判断，`link_target` 记录目标，`link_exists=false` 即断链。
- 硬链接：`st_nlink>1` 且 `(st_dev, st_ino)` 相同者聚合为一组。

### 3.4 恢复对账

- 库目录格式：JSON / CSV / BibTeX / RIS / SQLite（`catalog.json/csv/bib/bibtex/ris/sqlite/sqlite3/db`）。
- 每条目录记录取路径字段（`path/file/filename/filepath/name/relative_path/relpath/entry/id`）。
- `matches`：路径命中实际文件且 size/hash 一致；`mismatches`：路径命中但 size 或 sha256/md5 不一致；`missing`：路径未命中实际文件；`orphaned`：实际文件未被任何目录记录认领。
- hash 比对：64 位十六进制按 sha256 比，32 位按 md5 比；`checksum/hash/sha` 依长度自动选择。

### 3.5 索引

- `keywords` 按文件名中的字母数字与 CJK 连续段拆分（小写）。
- 索引与 `library_index.json` 可直接用于常见检索问答（计数、重复、缺失、断链、版本、作者/年份启发式）。

## 4. 降级码

| 码 | 含义 | 处置 |
|---|---|---|
| `too_large` | 超过 2MB 哈希上限 | 保留元数据，跳过 sha256/md5 |
| `unreadable` | 权限不足或 IO 错误 | 记录路径与原因，继续 |
| `symlink` | 符号链接 | 记录目标与是否存在，不跟随 |
| `non_regular` | FIFO/socket 等 | 保留类型，不做内容哈希 |
| `bad_json` | 库目录 JSON 解析失败 | 记录错误，继续其余材料 |
| `csv_parse_error` | CSV 解析失败 | 同上 |
| `sqlite_open_error` / `sqlite_query_error` | SQLite 打不开/坏表 | 记录，继续 |
| `stat_failed` | stat 失败 | 记录路径与异常 |

## 5. 预算与早落盘（本版强化点）

- 总预算 900 秒 / 64 次模型请求，脚本读写不计入请求。
- 阶段预算：骨架 ≤3 分钟（必须最先落盘 partial）；盘点 ≤2 分钟；语义归位 6–8 分钟；证据与复核 ≤2 分钟；定稿收尾。
- 每步结束通过 `--update patch.json --result ...` 增量落盘；`result.json` 任一时刻都可解析。
- `runtime.budget` 记录 `total_seconds/model_requests/phase/elapsed_s/requests_estimate`；`phase_times_s` 记录各脚本阶段真实耗时，供 Agent 判断剩余预算。

## 6. 证据与诚实标注

- `sources` 只接受逐字 `quote`；无原文处写 `not_reported` 或入 `pending_human`。
- 冲突来源全部记录并标 `source_conflict`，不隐藏。
- 缺失软件状态、科学数据、哈希、DOI 一律不编造。
