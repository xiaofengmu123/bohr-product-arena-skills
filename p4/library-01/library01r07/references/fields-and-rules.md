# 字段与规则字典（脚本机械层的行为规范）

本文件是 `scripts/core_impl.py` 确定性规则的可读说明，便于他人复跑、核对与二次开发。脚本只做机械层；语义层（目录最终归位、版本最终判定、检索问答的自然语言结论）由 Agent 在 `computed` 之上补全并经 `--update` 合并。

## 1. result.json 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 来自 case.json（`case_id`/`id`/`armiId` 等）或 task.md 的 `challenge_id`；兜底 `unknown-case`。非空。 |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | list | 每项 `{item, locator, quote}`；脚本自动为机械结论补证据 |
| `uncertainty` | list | 每项 `{item, reason, severity}` |
| `change_log` | list | 每项 `{time, actor, step, detail}`，UTC ISO-8601 |
| `errors` / `degradations` | list | `{item, detail, time}`；错误与降级分开记录 |
| `runtime` | object | `{started_at, duration_seconds, python, platform}` |
| `limitations` / `pending_human` | list | 明确缺口与待人工确认项 |
| `computed` | object | 机械证据容器，见下 |

`computed` 子键：`files`（全量盘点）、`summary`（计数）、`duplicate_groups`、`version_candidates`、`version_notes`、`aliases`、`reconciliation`、`answers`、`index`、`artifacts`。与保留键冲突的 contract 字段改存 `computed._contract_<字段>`。

## 2. 状态判定（确定性顺序）

1. 输入根不存在 → `input_error`
2. 无材料文件（仅根级 `case.json`/`task.md`）→ `insufficient_evidence`
3. `case.json` 缺失或不可解析 → `input_error`
4. `output_contract` 未识别 → `partial`（uncertainty 标 `output_contract` 缺失）
5. 存在 `errors` → `partial`
6. 其余 → `ok`

## 3. 盘点规则

- `os.walk(followlinks=False)`，`dirnames`/`filenames` 均排序，保证确定性。
- 符号链接目录记录为条目但不展开（防环）。
- 常规文件：记录 `size`/`mtime`(UTC)/`mode`(八进制)/`nlink`/`dev`/`ino`/`uid`/`gid` 与 `sha256`。
- 文件 > `MAX_HASH_BYTES`（256 MiB）→ 跳过哈希，`too_large=true`，`sha256=null`。
- 无法 `lstat`/读取 → `unreadable=true`，记入 `errors`，不中断。
- 非常规对象（FIFO/socket/设备等）→ `type=non_regular`。

## 4. 去重规则

- 仅对 `type=file` 且有 `sha256` 的条目分组；组内去重路径并排序。
- 组规模 > 1 才输出；排序键为 `(-count, sha256)`。

## 5. 版本候选规则（启发式，显式标注）

- 归一化主干：文件名去扩展名 → 小写 → 去除版本噪声（`v1`/`2.0`/`final`/`draft`/`old`/`copy`/`backup`/`bak`/`rev`/`edited`/`original` 等及括号/下划线）→ 压缩空白。
- 按 `(归一化主干, 扩展名)` 分组，组内按 `mtime` 升序成链。
- 相邻关系：`same_content`（哈希相等）、`size_delta`、`text_similarity`（仅小文本，`difflib` ratio）。
- 该组关系是**候选**，须由 Agent 结合题目规则最终确认。

## 6. 别名规则

- 符号链接：`type=symlink`，记录 `link_target` 与 `broken`（目标 `os.path.exists` 为假）。
- 硬链接：`(dev, ino)` 相同且 `nlink > 1` 的文件归为一组。

## 7. 恢复对账规则

- 库目录识别扩展名：`.json`/`.csv`/`.bib`/`.ris`/`.db`/`.sqlite`/`.sqlite3`。
- 记录路径字段优先级：`path`/`file`/`filename`/`file_path`/`relative_path`/`rel_path`/`location`/`uri`。
- 记录哈希字段优先级：`sha256`/`hash`/`md5`/`checksum`/`content_hash`。
- 输出：`matches`（路径在、哈希一致或无期望哈希）、`mismatches`（哈希不符或无法哈希）、`missing`（路径不在）、`orphaned`（实际文件未被任何目录引用，仅当解析到目录时计算）。

## 8. 索引与问答

- 索引：对路径、哈希前缀与目录记录中的 `title`/`author`/`year`/`doi`/`bibkey`/`ris_type`/`project`/`instrument` 分词（ASCII 词 + CJK 连续串），term → 排序路径列表。
- 机械问答字段：文件数、目录数、总字节、重复组数/冗余份数、版本候选组数、符号/断链/硬链接组数、对账四类计数、超大/不可读计数、扩展名分布。

## 9. 鲁棒性常量

| 常量 | 默认值 | 作用 |
|---|---|---|
| `MAX_HASH_BYTES` | 256 MiB | 超过则不哈希，记 `too_large` |
| `MAX_TEXT_READ_BYTES` | 4 MiB | 目录/元数据文本读取上限 |
| `MAX_DIFF_TEXT_BYTES` | 256 KiB | 版本文本差异比对上限 |

所有常量位于 `core_impl.py` 顶部，可按需调整；调整属于显式设计取舍，须在 `limitations` 中说明。
