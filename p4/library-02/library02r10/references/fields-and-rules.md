# 字段与规则（fields and rules）

本文件是脚本确定性层与 Agent 语义层共同遵守的约定。脚本只做机械、可复算的判定，
Agent 在其给出的候选上做语义判断，并用 `--update` 回写。两者均不编造数据。

## 1. 状态语义（status）

| 值 | 含义 | 触发条件（脚本自动） |
|---|---|---|
| `ok` | 全部机械阶段完成且无错误 | 全量运行、无 errors |
| `partial` | 有产物但存在可说明的缺口 | 有 errors/degradations，或骨架落盘 |
| `insufficient_evidence` | 无科研材料可处理 | 输入目录只有控制文件或无材料 |
| `input_error` | 输入缺失或不可解析 | case.json 缺失、输入根不存在 |

## 2. 元数据字段（library_manifest.csv 列）

`relpath, kind, role, size, mtime, perm_octal, ext, sha256, content_status,
link_target, inode, nlink`

- `relpath`：相对输入根的 `/` 分隔路径（跨平台、可复算）。
- `role`：`control`（case.json/task.md/问题文件）或 `material`（科研材料）。
- `content_status`：`hashed | too_large | unreadable | binary`；未哈希即标注原因。
- `kind`：`file | dir | symlink | broken_symlink | non_regular`。

## 3. 判定规则（均为启发式，需 Agent 复核）

- **去重**：按内容 SHA-256，与文件名无关；同名不同内容不合并。
- **版本候选**：归一化主干（NFKC + 小写 + 去除 version/copy/draft/final/修订/
  副本/扫描件等标记）分组，组内用 `difflib.SequenceMatcher` 比值估计版本邻近度；
  无法读文本时退化为大小比并标 `basis=size`。结果标 `heuristic=true`。
- **别名**：符号链接（含断链，记录 target）与硬链接（按 inode 且 nlink>1）分组。
- **对账**：库目录条目按 `path/filename` 或 `sha256/hash` 匹配磁盘文件，产出
  `matches / mismatches / missing / orphaned`。`mismatches` 指条目引用的路径不存在。

## 4. 证据回链（evidence backlink）

每个关键结论一条 `sources` 条目：`{item, locator, quote}`。

- `locator`：文件相对路径，或 `材料文件#页码/段落`；不能是"整篇文件"。
- `quote`：原文逐字片段（段落首句 + 命中片段），脚本给出机械候选，Agent 校正。
- 同一指标两来源不一致时，两条都记录并标 `source_conflict`。
- 缺失值写 `not_reported` 或放入 `pending_human`，不虚构。

## 5. 过滤条件（硬约束）

年份、项目编号、文件类型、实验批次、版本、权限等条件作为硬约束执行：条件无法
满足时返回 `empty_result` 并如实说明，不自动放宽。脚本的 `computed.answers` 中
未命中项显式标注"条件保持为硬约束，未放宽"。

## 6. 库目录格式（脚本自动解析）

JSON（对象/列表）、CSV（表头）、Markdown（表格）、BibTeX（`@type{key,...}`）、
RIS（`XX  - ...`，`ER  -` 分条）、SQLite（每表 SELECT）。打不开/坏表记录
`sqlite_open_error`/`sqlite_query_error`，不崩溃。

## 7. 确定性保证

目录遍历排序、JSON `sort_keys=True`、无随机数、记录 ID 由路径哈希派生；同一输入
两次运行除时间戳外结果一致。
