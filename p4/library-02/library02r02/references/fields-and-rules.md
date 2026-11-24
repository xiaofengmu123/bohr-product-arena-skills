# 字段与规则（fields-and-rules）

本文档说明脚本产物的字段含义、判定规则与证据定位约定，供复核与二次开发使用。

## 1. 文件记录字段（library_manifest.csv 与 result.result.files）

| 字段 | 含义 |
|-|-|
| `path` | 相对输入根目录的路径，统一用 `/` 分隔 |
| `name` / `ext` | 文件名 / 扩展名（小写） |
| `kind` | `file` / `dir` / `symlink` / `other` |
| `size` / `mtime` / `mode` | 字节数 / ISO 时间 / 权限字符串（如 `-rw-r--r--`） |
| `hash_sha256` / `hash_state` | 内容哈希 / `ok`、`too_large`、`unreadable`、`non_regular`、`symlink` |
| `symlink_target` / `symlink_broken` | 链接目标 / 是否断链 |
| `role` | `control`（case.json/task.md）或 `material` |
| `text_state` / `text_preview` | 文本提取状态 / 前 2000 字符预览 |

## 2. 判定规则

- **去重**：按内容 `SHA-256` 分组，完全不看文件名。
- **版本候选**：先按归一化主干（去掉版本号、final/draft/修订/扫描等噪声、小写化、去标点）分组，组内按内容相似度（difflib ratio）标注差异，`heuristic=true`。
- **别名**：符号链接（含断链，`broken=true`）单独列；硬链接按 `(st_dev, st_ino)` 分组。
- **行政版本说明**：解析 `/input` 下 JSON/CSV/Markdown 中的版本表，字段归一到 `file/version/status/date/note/id/project/batch/permission`。
- **库目录**：解析 JSON/CSV/Markdown/BibTeX/RIS/SQLite 中的登记条目，同样归一化。
- **对账**：登记条目与真实文件比对 → `matches`（含哈希校验）/`mismatches`（哈希不符）/`missing`（无对应文件）/`orphaned`（真实文件未被登记）。
- **索引**：文本类文件按词元（英文词 + 中文二字组）建倒排索引；PDF 若存在系统 `pdftotext` 则提取，否则标 `pdf_pending` 由 Agent 用提供的 pdf-text-tool 提取。
- **问答**：从 case.json 的 `questions/queries/asks` 与 task.md 提问行提取问题，做机械词元检索并执行硬过滤（year/filetype/version/project/permission），无法满足条件时如实报 `filter_unsatisfied`，不放宽。

## 3. 证据定位（sources）

每条关键结论附 `{ item, locator, quote }`：

- `locator` 用相对路径或 `materials/xxx.pdf#p3` 形式；PDF 页码指物理页，印刷页错位在 `uncertainty` 中说明。
- `quote` 必须是源文件逐字片段，不摘要改写。
- 同一指标两个来源冲突时两条都记录并标 `source_conflict`。

## 4. 状态与降级

| 状态 | 触发条件 |
|-|-|
| `ok` | 盘点与全部产物完成，无阻断性错误 |
| `partial` | 有错误/降级，或版本链与问答仅启发式 |
| `insufficient_evidence` | 无材料文件（只有控制文件或空目录） |
| `input_error` | 无 case.json 且无 task.md，或写盘失败兜底 |

禁止编造哈希、文件内容、元数据与运行结果；缺失一律 `not_reported` / `null` / `pending_human`。

## 5. 复跑约定

- 目录遍历与文件排序固定（`sort()`），哈希稳定，无随机数；仅时间戳与运行时长随运行变化。
- 全部源文件只读；中间产物在 `/app/work/`，最终产物在 `/app/output/`。
