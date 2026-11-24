# 字段字典与判定规则（与 scripts 行为一致）

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 从 case.json 的 `case_id`/`caseId`/`id`/`challenge_id` 识别 |
| `status` | string | `ok`/`partial`/`insufficient_evidence`/`input_error` |
| `<contract 字段>` | any | `output_contract` 原样回填；保留名冲突转 `computed._contract_<字段>` |
| `sources` | array | `{item, locator, quote}` 证据溯源 |
| `uncertainty` | array | `{item, reason, severity}` 不确定项 |
| `pending_human` | array | `{item, detail}` 需人工确认项 |
| `change_log` | array | `{time, actor, step, detail}` 操作时间线 |
| `errors` | array | `{code, path, detail}` 可恢复错误 |
| `degradations` | array | `{code, path, detail}` 降级/跳过项 |
| `runtime` | object | `wall_seconds`、`request_budget`、`request_budget_seconds` |
| `limitations` | array | 方法边界说明 |
| `computed` | object | 全部机械证据（见下） |
| `artifacts` | array | `{path, kind, role}` 产物清单 |

## 2. computed 子结构

- `files` / `inventory`：文件记录（`path/type/size/mtime/mode/sha256/
  hash_status/target/link_status/inode/dev`）。
- `summary`：文件数、目录数、已哈希数、重复组数、版本候选数、符号链接数、断链数、
  硬链接组数、对账四类计数。
- `duplicate_groups`：`{sha256, count, paths, evidence}`，按内容 SHA-256 分组。
- `version_candidates`：`{normalized_stem, heuristic, members[], replaces_chain,
  note}`。
- `version_notes`：`{source, line, raw, heuristic}` 行政版本说明逐字抽取。
- `aliases`：`{symlinks[], hardlink_groups[]}`（含断链）。
- `reconciliation`：`{matches, mismatches, missing, orphaned, summary}`。
- `catalogs`：识别到的库目录文件及其条目数。
- `index`：可检索索引（盘点记录 + 库目录记录合并）。
- `answers`：机械问答 `{question_key, answer, evidence}`。

## 3. 哈希与降级规则

- 内容哈希用 SHA-256，默认单文件上限 2 MiB（`--max-hash-bytes` 可调）。
- `hash_status` 取值：`ok` / `too_large` / `unreadable` / `symlink` /
  `non_regular` / `stat_error`；降级时保留路径、大小、权限与链接目标，不伪造哈希。
- 遍历 `os.walk` 后对目录与文件名做稳定排序，无随机数，重复运行除时间戳外一致。

## 4. 版本与别名启发式

- 版本：文件名归一化去 `v1/v2/final/draft/日期/副本` 等后缀，同主干按修改时间升序
  给出候选链；该结果为**启发式**，在 `uncertainty` 标注并要求人工确认。
- 别名：符号链接（含断链）与硬链接组（`dev+inode` 相同且同哈希）；断链记录
  `link_status=broken`。
- 行政版本说明：对 task.md/case.json 及 versions/changelog 类文件做关键字逐字抽取，
  不进行语义对齐，避免把猜测当成事实。

## 5. 对账判定

- 库目录（JSON/CSV/BibTeX/RIS/SQLite）声明的条目按 `path`/`file`/`L1` 等键定位，
  与盘点比对：路径存在且哈希一致 → `matches`；路径存在但哈希不一致 →
  `mismatches`；路径不存在 → `missing`；盘点有而目录未声明 → `orphaned`。
- 哈希不一致同时保留声明值与实际值，供复跑核对。

## 6. 状态判定顺序

1. case.json 缺失/不可读 → `input_error`。
2. 无任何文件 → `insufficient_evidence`。
3. 存在 `errors` 或 `degradations` → `partial`。
4. 其余 → `ok`。

Agent 在阶段 2 可依据语义修正 `status` 与各判定，但必须同步更新 `sources` 与
`change_log`。
