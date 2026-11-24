# 字段字典与判定规则（脚本确定层）

本文档规定 `finalize.py` 的确定性判定口径；语义层（字段映射、版本链确认、检索答案）由
Agent 在 `result.json` 的 `sources`/`uncertainty` 中补充，不得由脚本猜测。

## 1. 文件清单字段（library_manifest.csv / computed.files）

| 字段 | 含义 | 缺失/降级 |
|---|---|---|
| path | 相对 /input 的 POSIX 路径 | 始终存在 |
| name | 文件名（含扩展名） | 始终存在 |
| type | file / dir / symlink / other / unreadable | 无法 stat 时为 unreadable |
| size | 字节数 | symlink 为链接体大小，可缺失 |
| mtime | 修改时间（epoch 秒） | 无法 stat 时缺失 |
| mode | 权限（Linux 八进制 / Windows rw·ro） | 无法 stat 时缺失 |
| sha256 | 内容哈希 | 超 16MB 记 note=too_large；不可读记 hash_error |
| inode/nlink | 硬链接判定用 | 文件系统不支持时为 0，跳过硬链接分组 |
| link_target/resolved/broken | 符号链接目标/解析/断链 | 仅 symlink 出现 |
| text_encoding/preview | 文本类文件的编码与首 400 字符 | 非文本类型不产生 |

## 2. 去重规则

- 依据：`sha256` 完全一致（内容级，与文件名无关）。
- 同一哈希 ≥2 个文件记入 `duplicate_groups`，按组大小降序。
- 无哈希（too_large/unreadable）不参与去重，也不静默删除，仅记录 note。

## 3. 版本候选规则（启发式，需 Agent 复核）

1. 归一化主干 `norm_stem`：小写；剔除 `v1/v2/rev/final/draft/copy/bak/old/(n)/[n]` 与
   `YYYY-MM-DD` 等版本标记；非字母数字与中文折叠为 `_`。
2. 同主干 ≥2 个文件 → 版本候选链，按 mtime 升序排列。
3. 相邻差异给出 `size_delta` 与 `hash_changed`，`heuristic=true` 明确标注。

## 4. 别名识别

- 符号链接：`os.path.islink` + `readlink`，断链按 `os.path.exists` 判定。
- 硬链接：按 `(st_dev, st_ino)` 分组，组内 ≥2 个文件记入 `aliases.hardlinks`。
- 不支持 inode 的平台自动跳过，不报错。

## 5. 行政版本说明解析

- 扫描名字含 `version/版本/changelog/note/说明/历史/change/release` 的文本文件，
  逐行匹配含 `version/版本/修订/release/changelog/history/变更` 的行。
- 每行记录 `source` + `locator`（`path#L行号`）+ 逐字 `quote`。
- task.md 中含“版本说明/行政版本”的行同步记录，来源标 `task.md`。

## 6. 库目录解析与恢复对账

- 支持 JSON（list 或单层嵌套 list）、CSV、BibTeX、RIS、SQLite。
- 条目键优先级：path/file/file_path/filename/name/key/id/DOI。
- 对账口径：`matches`=目录条目键与磁盘路径/文件名重合；`missing`=目录有而磁盘无；
  `orphaned`=磁盘有而目录未引用；`mismatches` 需白板哈希/大小，无白板时保持空。
- SQLite 打不开/坏表记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。

## 7. 检索索引

- 关键词 = 文件名分词 token、`ext:<扩展名>`、类型、库目录 author/year/title/journal 分词。
- 值为该关键词命中的文件路径（有序去重）。

## 8. 机械问答（computed.answers）

自动给出：文件总数、目录/空目录数、重复组数、断链符号链接数、硬链接组数、
版本候选链数、对账 matches/missing/orphaned 数、库目录源数；每题带 `evidence` 定位。
