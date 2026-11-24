# 字段与规则说明（fields-and-rules）

本文件说明 `library_manifest.csv`、`dedup_version.json`、`recovery_report.json`、`library_index.json` 的字段与判定规则，供他人按记录复跑与核对。

## 1. 清单字段（library_manifest.csv，每行一个源文件）

| 字段 | 含义 |
|---|---|
| `relpath` | 相对 /input 的 POSIX 路径（`/` 分隔） |
| `type` | `regular` / `dir` / `symlink` / `special` |
| `role` | `material`（科研材料）或 `task_input`（case.json / task.md） |
| `size` | 字节数；目录/符号链接为 null |
| `mtime` | 修改时间（ISO 8601 UTC） |
| `mode` | 权限八进制（如 `0o644`） |
| `uid` / `gid` / `inode` / `nlink` | 系统元数据（Windows 上可能为 null） |
| `symlink_target` / `broken` | 符号链接目标与是否断链 |
| `sha256` | 内容 SHA-256（十六进制小写） |
| `hash_status` | `ok` / `empty` / `too_large` / `unreadable*` / `symlink` / `non_regular` |
| `encoding` | 文本预览所用编码（`utf-8` / `gbk` / `latin-1` / `empty` 等） |
| `catalog_kind` | 由扩展名识别的库目录类型（json/csv/bibtex/ris/sqlite） |

## 2. 判定规则

- **去重**：仅按内容 SHA-256 分组，`dedup_version.json > duplicate_groups` 列出 >1 文件的组。同名不同内容不算重复。
- **版本候选**：文件名去掉日期、版本后缀（`v1`/`v2`/`final`/`draft`/`副本`/`备份`/`(...)` 等）得到归一化主干；同主干 ≥2 个文件按 mtime 升序成链，相邻对给 `identical`/`size_delta`/`similarity`（difflib）或 `binary_or_unreadable`。**全部标 `heuristic`，供 Agent 语义复核**。
- **别名**：符号链接（含断链）逐条记录；硬链接组按 `(st_dev, st_ino)` 且 `nlink>1` 分组。
- **对账**：库目录记录中的 `file`/`path`/`filename` 等键映射到磁盘 `relpath`（先精确、再相对、再唯一 basename），产出 `matches` / `mismatches`（目录哈希与实际 SHA-256 不一致）/ `missing`（目录有、磁盘无）/ `orphaned`（磁盘有、任何目录均未引用）。
- **索引**：文件名、目录名、库目录字段值分词后建倒排索引（token → relpath），并抽取 author/year/title/doi/type 字段。

## 3. 库目录解析

- **JSON**：顶层 list 逐项；顶层 dict 的每个 list/dict 值作为一组记录。
- **CSV**：`csv.DictReader`，跳过全空行。
- **BibTeX**：正则按 `@type{key, field={...}}` 解析。
- **RIS**：按 `TY/ER` 分段，`AU/TI/PY/DO/JO/VL/IS/SP` 映射到 author/title/year/doi/journal/volume/issue/pages。
- **SQLite**：只读连接，遍历全部表与全部行，字段值二进制转 UTF-8；打开失败记 `sqlite_open_error`，单表查询失败记 `sqlite_query_error`，不中断。

## 4. 证据溯源

每个关键结论在 `result.json > sources` 有 `item + locator + quote`：`locator` 为文件相对路径或 `.`（汇总），`quote` 为逐字原文或确定性计算结果；目录哈希与实际哈希冲突时两条都保留并标 `mismatch`。缺失/无法判断写入 `pending_human` 或 `not_reported`，禁止编造。

## 5. 复跑核对

同输入重复运行：遍历按名称排序、哈希稳定、JSON 键序固定、无随机数，因此除 `runtime`/`change_log` 时间戳外结果一致；可直接 `diff` 两次 `result.json` 的 `computed` 部分核对。
