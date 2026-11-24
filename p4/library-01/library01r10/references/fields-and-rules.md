# 字段字典与处理规则（fields-and-rules）

本文定义 Skill 各产物/中间表示（IR）的字段与确定性规则，供复核者与后续 Agent 使用。

## 1. status 取值

| 值 | 含义 | 触发条件 |
|---|---|---|
| `ok` | 完成，结论有证据支撑 | 正常完成且无 case 级错误 |
| `partial` | 部分完成，存在已知缺口 | 骨架阶段；或渲染/写入失败 |
| `insufficient_evidence` | 材料不足 | 输入根不存在或没有任何文件/目录 |
| `input_error` | 输入错误 | case.json 缺失/坏 JSON/非对象 |

## 2. output_contract 识别与回填

- 候选键名（按顺序）：`output_contract`、`output`、`result_schema`、`contract`、
  `outputSchema`、`result_schema`；脚本也从 `data["output"]`/`data["result"]` 内嵌套查找。
- 回填规则：dict 值缺省为 `{}`，list 值缺省为 `[]`，其余缺省为 `null`；键名不变、未知键不删。
- 保留键冲突：contract 键与 `case_id/status/sources/...` 冲突时改存 `computed._contract_<字段>`。
- 机械自动填充（名称匹配才填，其余留给语义层）：`files/manifest/inventory`→computed.files，
  `summary`→computed.summary，`duplicates/duplicate_groups`→computed.duplicate_groups，
  `versions/version_candidates`→computed.version_candidates，`aliases`→computed.aliases，
  `reconciliation/recovery`→computed.reconciliation，`index`→computed.index，
  `answers`→computed.answers，`manifest_path/recovery_report/dedup_version/index_file/
  operation_log/report`→对应产物文件名。未填充字段记入 `uncertainty`（`contract_field_unfilled`）。

## 3. 文件条目（IR `computed.files[].*` / manifest CSV 列）

`rel_path, abs_path, name, kind, size, sha256, sha256_status, mtime, ctime, permissions,
uid, gid, inode, nlink, extension, mime_hint, is_binary, target, depth, is_hidden`

- `kind`：`file | dir | symlink | broken_symlink | other | unreadable`。
- `sha256_status`：`hashed | too_large | symlink | non_regular | unreadable | not_hashed`。
- `permissions`/`mode`：八进制字符串（如 `0o644`）；`uid/gid` 保留运行时数值。
- 内容哈希上限：默认 2 MiB（`DEFAULT_HASH_LIMIT`），超过者只记录路径/大小/权限/类型。
- 类型判定：扩展名映射表 + 魔数头部（`%PDF`、`SQLite format 3`、`PK`、`\x89PNG` 等）；
  仅做启发式，完整内容解析留给语义层。

## 4. 去重与版本规则

- 去重：仅对 `kind=file` 且 `sha256_status=hashed` 的条目按 SHA-256 分组，组内 `count ≥ 2`。
- 版本候选：先归一化主干（NFKC 小写、去扩展名、去除 `v/ver/version/rev N`、
  `final/draft/copy/backup/old/new`、`YYYY-MM-DD`、`YYYYMMDD`、`(1)/(2)/copy N` 等 token），
  按 `(归一化主干, 扩展名)` 分组；组内 `≥2` 个不同哈希 → 候选链，按 mtime 升序，
  相邻做文本相似度（difflib，上限 128 KiB）。候选一律标 `heuristic=True`。

## 5. 别名规则

- 符号链接：`lstat` 识别；`os.stat` 失败判 `broken_symlink`，`target` 存 `os.readlink` 结果。
- 硬链接：`nlink > 1` 的 regular file 按 `st_ino` 分组，组内 `≥2` 个路径记为硬链接别名组。

## 6. 库目录解析与对账

- 支持的库目录格式：JSON（list/dict）、CSV（首行表头）、BibTeX（`@type{key, field={...}}`）、
  RIS（`TY  -`/`ER  -` 记录）、SQLite（只读打开，逐表转 dict，每表限 1000 行）。
- 目录发现：扩展名 ∈ {json,csv,bib,ris,sqlite,db,sqlite3}，或文件名命中
  `manifest|catalog|index|library|inventory|list|records|zotero|reference`。
- 对账路径字段：依次尝试 `path/file/filename/location/rel_path/relative_path/filepath/url/name/id`；
  相对路径先去 `./`；无法按路径命中时回退到 basename 唯一匹配。
- 分类：`matches`（路径存在且哈希一致或目录未给哈希）、`mismatches`（哈希不一致）、
  `missing`（目录有而文件无）、`orphaned`（文件有而目录未引用）。
- 哈希字段候选：`sha256/hash/checksum/md5`。

## 7. 确定性保证

- 遍历用 `os.walk(topdown=True, followlinks=False)`，`dirnames`/`filenames` 均排序；
  输出 JSON 用 `sort_keys=True`；无随机数、无并发、无网络。
- 时间字段仅出现在 `runtime` 与 `change_log`（骨架/每次 merge 追加一条）；其余字段跨运行一致。

## 8. 编码与错误处理

- 文本统一按 `utf-8-sig` → `utf-8` → `latin-1` 顺序解码，坏编码不崩溃。
- JSON 写盘用临时文件 + `os.replace` 原子替换；`errors`/`degradations` 保存所有
  `unreadable`、`hash_failed`、`bad_json`、`bad_csv`、`sqlite_open_error`、`sqlite_query_error`、
  `listdir_failed`、`write_failed`、`input_root_missing`、`case_json_missing` 等具体原因。
