# 字段字典与规则（fields and rules）

本文件是 `scripts/core_impl.py` 的字段字典与判定规则说明，供 Agent 复核脚本产出并做语义归位时对照。所有字段均可由脚本从真实输入确定性复算，不依赖外部词表或个人路径。

## 1. 清单字段（library_manifest.csv，每行一个源文件）

| 字段 | 含义 | 取值与规则 |
|---|---|---|
| `relpath` | 相对输入根路径 | `os.path.relpath` 后用 `/` 分隔，跨平台稳定 |
| `name` / `ext` | 文件名 / 扩展名 | 由 `os.path` 拆分，扩展名小写 |
| `kind` | 文件类别 | `regular` / `symlink` / `directory` / `fifo` / `socket` / `char_device` / `block_device` / `other` |
| `size` | 字节数 | 来自 `lstat`；目录/非常规文件也记录 |
| `mode_octal` / `mode` | 权限 | 八进制 + `stat.filemode` 可读串 |
| `mtime` / `mtime_epoch` | 修改时间 | ISO 8601 UTC + epoch 秒；用于版本链排序 |
| `uid` / `gid` / `nlink` / `dev` / `ino` | 属主/链接数/设备/inode | `nlink>1` 与 `(dev,ino)` 用于硬链接别名 |
| `sha256` | 内容哈希 | 仅常规文件；超大/不可读/非常规为 `null` |
| `status` | 处理状态 | `ok` / `too_large` / `unreadable` / `non_regular` / `symlink` |
| `link_target` / `broken` | 链接目标 / 是否断链 | 仅符号链接 |
| `error` | 错误信息 | 仅当读取/哈希失败时填写 |

## 2. 去重与版本规则

- **去重**：只按内容 `sha256` 分组，忽略文件名、扩展名与路径；组内 `paths` 升序，结果确定性。
- **版本候选**：归一化主干（去掉 `v1/v2/final/draft/old/new/backup/copy/副本/备份/YYYY-MM-DD/revN` 等后缀）+ 内容哈希 + `mtime` 升序排列。**这是启发式**，脚本在每条记录中写 `"heuristic": true`，须由 Agent 语义确认后才可作为最终版本链。
- **内容相同 vs 版本不同**：同主干且哈希相同 → 属于重复组而非版本差异；同主干且哈希不同 → 版本候选，记录 `distinct_hashes` 与 `content_identical:false`。
- **别名**：符号链接（含断链）单列；硬链接按 `(dev,ino)` 聚合为 `hardlink_groups`。

## 3. 行政版本说明解析

- 来源：`task.md` 与文件名匹配 `version|versions|note|notes|changelog|change_log`（大小写不敏感）的常规文件。
- 模式：`A -> B` / `A => B` / `A → B`、`replaces/supersedes/replaced-by: B`、`version: N`。
- 每条说明带 `source` + `locator`（`文件名#L行号`）+ 逐字 `quote`；无法解析的行原样保留为说明，不补造。

## 4. 库目录/清单解析（恢复对账用）

| 类型 | 扩展名/特征 | 输出 |
|---|---|---|
| JSON | `.json` 且名含 `manifest/library/inventory/index/catalog/registry/files/metadata` | 递归抽取 `relpath/path/file/filename/name/file_path` 条目 |
| CSV | `.csv` | `DictReader`，首行作表头，映射到条目 |
| BibTeX | `.bib` | 抽取 `title/author/year/doi/journal` |
| RIS | `.ris` | 抽取 `TI/AU/PY|Y1/DO/JO|T2` |
| SQLite | `.sqlite/.sqlite3/.db` | 只读连接，枚举表并抽取可映射条目 |

- 对账四类：`matches`（清单有且哈希一致）、`mismatches`（清单有但哈希不一致）、`missing`（清单有但磁盘无）、`orphaned`（磁盘有但清单无）。

## 5. 输出契约对齐（本版重点）

- 契约来源：递归查找 case.json 中 `output_contract/output/outputContract/result_schema/resultSchema/contract` 键；JSON Schema 取 `properties`/`required`，普通 dict 取顶层键。
- 每个契约字段回填到 `result.json` 顶层，缺值给 `null` 或 `[]`；与保留键（`case_id/status/sources/uncertainty/change_log/errors/degradations/runtime/limitations/pending_human/computed/_contract_meta/artifacts`）冲突时改存 `computed._contract_<字段>`。
- 字段名到计算值的映射表见 `core_impl.py::FIELD_ALIASES`；`contract_alignment.json` 与 `result.json["_contract_meta"]` 逐条记录 `field/status(filled|empty|moved)/value_path/type`。
- `python3 -B scripts/main.py --check result.json --case-json case.json` 会校验每个契约字段是否已回填，缺失时退出码 2。

## 6. change_log 规范

- 每条：`{seq, time, actor, step, detail, fields_affected}`，`time` 为 UTC ISO 8601。
- 脚本每个阶段追加一条；`--update` 合并语义 patch 时自动追加一条并列出受影响字段。
- `operation_log.jsonl` 与 `result.json["change_log"]` 内容一致，可重放复核。

## 7. 确定性约定

- 遍历用 `os.walk` 并对 `dirnames`/`filenames` 升序排序；所有分组键排序；无随机数、无第三方依赖。
- 同一输入重复运行，除 `runtime` 与 `change_log[*].time` 时间戳外结果逐字节一致（`selftest.py` 已覆盖）。
