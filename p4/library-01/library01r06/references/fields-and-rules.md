# 字段字典与判定规则（fields-and-rules）

本文件是 `library-document-repair` 的字段字典、命名/去重/版本启发式与对账规则说明，
供 Agent 在脚本给出的候选上做语义补充时对照，保证 `plan.json` 与 `library_model.json`
的可复现语义。

## 1. 文件清单字段（library_manifest.csv 每行一个源文件）

| 字段 | 含义 | 取值/示例 |
|---|---|---|
| `rel` | 相对输入根目录的路径（正斜杠） | `materials/data_v1.txt` |
| `type` | 节点类型 | `file` / `symlink` / `non_regular` / `unknown` |
| `size` | 字节数 | 整数或 `null` |
| `mode` | 权限（八进制低 12 位） | `0o644` |
| `mtime` | 修改时间（本地 ISO） | `2026-01-02T03:04:05+0800` |
| `sha256` | 内容哈希（十六进制 64 位） | `9bf1cd...` 或 `null` |
| `status` | 处理状态 | `ok` / `too_large` / `unreadable` / `symlink(_broken)` / `non_regular` |
| `link_target` | 符号链接目标 | 路径或 `null` |
| `link_broken` | 符号链接是否断链 | `true` / `false` |
| `nlink` | 硬链接计数 | 整数 |
| `ext` / `stem` | 扩展名（小写含点）/ 去扩展名主干 | `.txt` / `data_v1` |

规则：路径按 `sorted()` 稳定遍历；`sha256` 只对常规文件计算；超过
`MAX_HASH_BYTES`（默认 32 MiB）标 `too_large` 并保留大小/权限/时间；符号链接不哈希。

## 2. 去重规则（按内容，不按文件名）

- 以 `sha256` 分组，同一哈希出现 ≥2 个不同 `rel` 记为 `duplicate_groups`。
- `summary.duplicate_extra_files = Σ(count - 1)`，即去掉每组首份后的冗余份数。
- 同内容不同名 = 重命名/别名，不是版本差异；不同哈希才进入版本候选。

## 3. 版本候选规则（归一化主干 + 内容差异，启发式）

`version_candidates` 按以下步骤生成，并固定标 `heuristic: true`：

1. 去掉扩展名，得 `stem`。
2. `normalize_stem(stem)`：替换括号/连字符/点号/下划线为空格，删除尾部版本标记
   （`v1`、`rev2`、`final`、`draft`、`old`、`new`、`copy`、`bak`、`YYYYMMDD`、
   `YYYY-MM-DD` 等），小写并压缩空白。
3. 同一归一化主干 ≥2 个常规文件 → 候选链，成员按 `mtime` 升序排列；
   `distinct_content_hashes` 记录实际内容差异数。

该规则是启发式，可能把同名但无关文件并入；Agent 需结合内容与题目规则确认，必要时在
`patch.json` 中通过 `sources`/`uncertainty` 修正或标注。

## 4. 别名（符号链接 + 硬链接）

- 符号链接：记录 `path`、`target`、`broken`（目标不存在时）。
- 硬链接组：以 `(st_dev, st_ino)` 分组，`nlink > 1` 且 ≥2 成员记入 `hardlink_groups`。
- 断链符号链接单独在 `summary.broken_symlinks` 计数，并在目录树标 `[断链]`。

## 5. 库目录解析（catalogs）

按扩展名自动识别：`.json`、`.csv`、`.bib`/`.bibtex`、`.ris`、`.db`/`.sqlite`/`.sqlite3`。
每条 `catalogs[].rows[]` 保留源文件与原始字段；解析失败写 `status`（如
`bad_json`、`csv_error`、`sqlite_open_error`、`sqlite_query_error`）并进入
`catalog_parse_issues` 与 `degradations`，不中断其余文件。

## 6. 恢复对账（reconciliation）

以 `path`/`filename`/`rel`/`sha256`/`key`/`id`/`doi`/`title` 为标识，把库目录行与磁盘
文件对齐：

- `matches`：目录行命中文件，且关键字段（size/sha256）无冲突。
- `mismatches`：命中但 size 或 sha256 冲突（记录具体冲突）。
- `missing`：目录行在磁盘上找不到对应文件。
- `orphaned`：磁盘文件未被任何目录行引用。

该对齐是启发式（标识匹配），结论需 Agent 结合题面复核。

## 7. 证据对象（evidence / result.json 的 sources）

每条证据字段：

| 字段 | 含义 |
|---|---|
| `id` | `e<n>` 稳定编号 |
| `item` | 结论描述 |
| `locator` | `task.md#L<n>`、`case.json#<key>`、文件相对路径或 `library_model.json#<区>` |
| `quote` | 逐字原文（`quote_kind=text`）或机器确定性事实串（`quote_kind=machine`） |
| `origin` | `mechanical`（脚本）或 `agent`（语义补充） |
| `status` | `confirmed` / `conflict` / `unverified` |
| `key` | 是否进入 `result.json.sources` |

## 8. 输出契约适配（plan.json）

- 识别键：`output_contract` / `output` / `result_schema` / `contract` / `outputs` / `result`。
- 字段默认值：字典规格按 `default`，否则按 `type`（`array/list`→`[]`，`object`→`{}`），
  其余 `null`；字段名含 `files`/`items`/`entries`/`records` 等列表提示时默认 `[]`。
- 与保留键（`status`/`sources`/`computed` 等）冲突时，契约值改存
  `computed._contract_<字段>`，并在 `plan.contract_clashes` 记录。
- 契约缺失时 `contract_source=not_reported`，并在 `uncertainty` 标注。

## 9. 确定性约定

同一输入重复运行，除时间戳（`mtime`、`created_at`、`change_log[].time`、`runtime`）外，
`result.json`/`plan.json`/`library_model.json` 的其余内容必须一致：遍历排序、哈希稳定、
无随机数；`--verify` 重渲染比对时剔除时间戳字段。
