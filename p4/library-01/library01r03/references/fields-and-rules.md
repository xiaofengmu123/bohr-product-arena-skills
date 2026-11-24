# 字段字典与规则（fields-and-rules）

本文件定义 Skill 产物的字段、命名规范化、版本判定、契约回填与降级标记，供他人复核与复用。

## 1. `result.json` 字段字典

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 案例标识，从 case.json 的 `case_id`/`caseId`/`id`/`challenge_id` 或 task.md 的 `challenge-*` 识别 |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error`，诚实标注 |
| `sources` | array | 关键结论证据，元素 `{item, locator, quote}` |
| `uncertainty` | array | 不确定项，元素 `{item, severity, reason}` |
| `pending_human` | array | 需人工补录/判断的缺口 |
| `errors` | array | 处理中遇到的错误，元素含 `path`/`kind`/`detail` |
| `degradations` | array | 降级处理记录（含写盘失败） |
| `runtime` | object | `started_at`/`elapsed_seconds`/`python`/`platform`/`argv`（唯一非确定字段） |
| `limitations` | array | 能力边界与未覆盖项 |
| `change_log` | array | 时间线，元素 `{time, actor, step, detail}` |
| `computed` | object | 机械证据：`summary`/`files`/`inventory`/`duplicate_groups`/`version_candidates`/`version_notes`/`aliases`/`reconciliation`/`catalogs`/`index`/`answers`/`_contract` |
| `artifacts` | object | 产物名 → 输出目录内相对路径 |

## 2. 逐文件清单（library_manifest.csv）列

`rel_path, type, size, mtime, sha256, encoding, mode_octal, nlink, inode, link_target, content_note`

- `type`：`file` / `dir` / `symlink` / `broken_symlink` / `other` / `unknown`。
- `content_note`：`too_large` / `unreadable` / `non_regular` / `broken_symlink` / `unhashed` / `symlink_ok`，无异常为 `null`。
- `encoding`：`text` / `binary` / `undecodable`，非常规文件为 `null`。

## 3. 目录树（directory_tree.txt）约定

- 以 `rel_path` 为树结构，子项按 `(casefold, raw)` 排序。
- 目录以 `/` 结尾；空目录下一行标 `[空目录]`。
- 符号链接标 `-> target`；断链额外标 `[断链]`。

## 4. 去重规则

- 仅对 `type=file` 且成功计算 SHA-256 的文件分组；分组键为 `(sha256, size)`。
- 组内成员 ≥ 2 才记为重复组；绝不按文件名判断。

## 5. 版本链规则（启发式，标注 `heuristic: true`）

- 主干归一化：取 basename 去扩展名 → 小写 → 非字母数字/中文替换为空格 → 移除 `v|ver|version|rev` + 数字、`final|draft|copy|backup|old|new` 等词 → 合并空白。
- 归一化主干相同的 ≥ 2 个文件组成候选组；按 `mtime` 升序排列成链，记录 `superseded_by`/`supersedes`。
- 相邻版本用 `difflib.SequenceMatcher` 计算 `lines_added`/`lines_removed`/`similarity_ratio`（文本 ≤ 2MB，否则 `diff: null`）。

## 6. 别名规则

- 硬链接组：`nlink > 1` 且 `(st_dev, st_ino)` 相同的文件为一组。
- 符号链接：记录 `target` 与 `broken`（`os.stat` 失败即断链）。

## 7. 行政版本说明解析

扫描 `.md/.txt/.rst/.yaml/.yml/.json`，逐行匹配：

- 版本号：`(?i)version\s*[:=]?\s*(v?\d+(?:\.\d+)+...)` → kind `version`。
- 替代声明：`supersede|replace|deprecate|obsolete` → kind `supersede`。
- 日期：`20xx-yy-zz` 且行内含版本关键词 → kind `date`。

每条输出 `{source, line, kind, value, quote}`。

## 8. 库目录解析与对账

| 格式 | 解析方式 |
|-|-|
| JSON | 数组/对象递归展开为记录，附 `_source`/`_index` |
| CSV/TSV | `csv.DictReader`，每行一条记录 |
| BibTeX | 正则提取 `@type{key, fields}` 与 `key = {value}` 字段 |
| RIS | `TY` 起始记录，`TI/AU/PY/DO/JO` 映射字段 |
| SQLite | `sqlite_master` 列出表，`SELECT * LIMIT 200` 取列与行 |

对账输出四类：`matches`（记录文件名命中文件 basename）、`mismatches`（记录带路径但文件不存在）、`missing`（记录无对应文件）、`orphaned`（文件未被任何记录引用）。文件名匹配键优先级：`file/filename/path/name/title/attachment/file_path/source/fulltext/pdf`。

## 9. 契约回填规则

- 契约键识别顺序：`output_contract` / `output` / `result_schema` / `contract`。
- 契约可为 dict（含 `properties`/`fields` 或直接字段名）或 list（字符串或 dict）。
- 每个字段缺省值：类型 `array` → `[]`，`object` → `{}`，其余 → `null`。
- 与保留键冲突的契约字段改存 `computed._contract_<字段>`；契约原始体存 `computed._contract`。

## 10. 降级标记字典

| kind | 含义 |
|-|-|
| `too_large` | 超过内容哈希/文本上限，跳过内容处理 |
| `unreadable` | 打开/读取失败 |
| `non_regular` | FIFO/socket/设备等非常规文件 |
| `symlink` / `broken_symlink` | 符号链接 / 断链 |
| `bad_encoding` | 文本无法以 utf-8/cp1252 解码 |
| `bad_json` | JSON 解析失败 |
| `missing` | case.json 等缺失 |
| `scandir_error` / `stat_error` | 目录遍历或元数据读取失败 |
| `sqlite_open_error` / `sqlite_query_error` | SQLite 打不开或查询失败 |
| `write_failed` | 产物写盘失败（保留骨架，退出码 2） |

## 11. 确定性保证

- 全部遍历与字典键按 `(value.casefold(), value)` 稳定排序；无随机数、无多线程。
- `computed` 只依赖输入树与固定常量；`runtime` 与 `change_log[*].time` 为唯一时间戳位置。
- `scripts/selftest.py` 归一化时间戳后比对两次运行的全部产物，用于交付前验证。
