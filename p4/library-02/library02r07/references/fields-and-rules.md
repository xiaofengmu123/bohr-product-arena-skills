# 字段与规则说明（fields-and-rules）

本文是 `scripts/core_impl.py` 的字段语义与判定规则的唯一权威说明；脚本实现以本文为准，避免文档与实现漂移。

## 1. 清单字段（library_manifest.csv 每行）

| 字段 | 类型 | 语义 |
|---|---|---|
| `path` | string | 相对输入根的路径，`/` 归一化为平台分隔符 |
| `type` | string | `file` / `dir` / `symlink` |
| `size` | int | `os.lstat().st_size`；目录记 0 |
| `mtime` | string | ISO8601 UTC，`None` 表示不可读 |
| `mode_octal` | string | 权限位八进制，如 `0o644` |
| `readable` | bool/null | 是否可打开读取（仅 file） |
| `is_control` | bool | 是否为控制文件（case.json/task.md/resources.yaml） |
| `sha256` | string | 内容 SHA-256，跳过时为空 |
| `hash_status` | string | `ok` / `too_large` / `non_regular` / `unreadable` / `stat_error` |
| `link_target` | string | 符号链接目标，断链时仍记录 |
| `link_broken` | bool | 符号链接目标是否存在 |

## 2. 去重规则

- 仅对 `type=file` 且 `sha256` 可算的条目分组；相同 `sha256` 且成员数 > 1 为一组。
- 按内容判定，**不按文件名**；组内路径排序稳定。
- 超大/非常规/不可读文件不参与分组，保留其 `hash_status` 原因。

## 3. 版本候选规则（启发式）

- 归一化主干 `normalize_stem`：去一个扩展名 → 小写 → 分隔符归一 → 去除版本标记（`v1`/`rev2`/`ver1.0`）→ 去尾随数字 → 去噪声词（draft/final/copy/副本/最终/修订等）。
- 同一主干下成员 ≥ 2 才形成候选链；两两内容相似度用 `difflib.SequenceMatcher`（文本前 20KB）。
- 所有版本链标记 `heuristic:true`，**必须人工/语义确认**后才能当作同一逻辑文档。

## 4. 别名规则

- 符号链接：`os.lstat` 判定 `S_ISLNK`；断链 = `os.path.exists(目标)` 为假。
- 硬链接组：`st_ino` 相同且 `st_nlink>1`；Windows 上以 `os.stat` 可用字段为准，不可用时跳过。

## 5. 行政版本说明 / 库目录解析

- JSON：递归展平 dict-of-dicts / list-of-dicts 为记录。
- CSV：`utf-8-sig` + `csv.DictReader`。
- Markdown/txt：识别 `|...|` 表格（表头行 + 分隔行）。
- BibTeX：正则解析 `@type{key, ...}` 的字段。
- RIS：按 `ER  -` 分记录，两字符标签映射字段。
- SQLite：只读连接，遍历全部表，逐行 dict 化；失败记录具体错误。

## 6. 恢复对账规则

- 记录中的路径字段候选：`path/file/filename/file_path/relative_path/location/url/local_path/附件/文件/路径`。
- 命中：归一化路径（`\`→`/`、去 `./` 前缀）落在实际文件集 → `matches`。
- 无路径字段 → `mismatches`（reason=no_path_field）；有路径但未命中 → `missing`。
- 实际文件未被任何记录引用 → `orphaned`。
- 计数汇总在 `counts`；`orphaned` 排序稳定。

## 7. 机械问答规则

支持的问题模式（关键词，大小写不敏感）：

| 模式 | 返回 |
|---|---|
| 文件数量（几个/多少/how many + file） | 材料文件数 |
| 目录数量 | 目录数 |
| 重复（duplicate/dup） | 重复组统计 |
| 断链（broken/symlink/符号链接） | 断链列表 |
| 缺失（missing） | catalog missing |
| 未登记（orphan/unregistered） | orphaned 列表 |
| 版本（version） | 版本候选链列表 |
| 哈希（hash/sha） | 已哈希文件清单 |
| `search: <词>` / `检索：<词>` | 文本索引逐行命中（file+line+quote，上限 100 条） |

不匹配任何模式 → 记入 `pending_human`，不编造答案。

## 8. result.json 保留键（contract 冲突处理）

`case_id, status, sources, uncertainty, pending_human, errors, degradations, runtime, limitations, change_log, computed` 为保留键；`output_contract` 中同名字段回填到 `computed._contract_<字段>`。

## 9. 确定性

- 目录遍历 `os.walk` 的 `dirnames`/`filenames` 均排序；所有聚合 dict 输出以 `sort_keys=True` 序列化。
- 无随机数、无环境依赖（除系统时间戳隔离在 `runtime`/`change_log`）。
