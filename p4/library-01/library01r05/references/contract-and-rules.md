# 输出契约与字段规则（contract-and-rules）

本文说明 Skill 如何把 `result.json` 逐字段对齐题目 `output_contract`，以及六类要素
（文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源）的落盘位置。

## 1. 契约发现

运行时从 `/input/case.json` 中按以下键名（顺序）识别契约，识别到第一个非空值即用：

`output_contract` → `output` → `result_schema` → `contract` →
`output_schema` → `result_contract` → `outputContract` → `resultSchema`。

若一层未命中，再在 case.json 的每个 dict 值里找一轮（部分案例把契约嵌套在
`result`/`task` 下）。全部未命中时，回退解析 `/input/task.md` 中反引号包裹的字段名
（`` `field_name` ``），把这些字段名作为标量字段；仍为空则仅产出元数据字段，并在
`uncertainty` 标注 `output_contract` 缺失。

## 2. 契约节点分类

契约节点分为四类，回填规则如下：

| 类别 | 典型写法 | 回填结果 |
|---|---|---|
| `container` | `{"title": null, "author": null}`（非空 dict） | 递归回填子字段 |
| `object_leaf` | `{}` 或 `{"type": "object"}` | 按字段名映射机械证据；无映射给 `{}` |
| `array_leaf` | `[]` 或 `{"type": "array"}` | 按字段名映射机械证据；无映射给 `[]` |
| `scalar_leaf` | `null` / `""` / `0` / `false` | 按字段名映射 summary 计数；无映射给 `null` |

JSON Schema 风格（含 `type` 与 `properties`）同样支持：`object+properties` 视为
container，`object`（无 properties）为 object_leaf，`array` 为 array_leaf，
`string/integer/number/boolean` 为 scalar_leaf。

## 3. 字段名映射（name-hint）

| 契约字段名（正则） | 类型 | 回填来源 |
|---|---|---|
| `manifest` / `file_list` / `inventory` | array | 文件清单记录（rel_path、sha256、size、权限、格式等） |
| `summary` / `overview` / `stat` | object | 统计摘要（计数、字节、按扩展名分布） |
| `duplicat` | array | 重复组（按内容 SHA-256） |
| `version` | array | 版本候选链（归一化主干 + mtime，标 `heuristic: true`） |
| `alias` / `symlink` / `hardlink` / `link` | object | 别名（符号链接含断链 + 硬链接组） |
| `reconcil` / `recover` / `missing` / `orphan` / `mismatch` | object | 恢复对账（matches/mismatches/missing/orphaned） |
| `index` / `search` | object | 可检索索引摘要 |
| `answer` / `question` / `qa` / `query` | array | 机械检索问答 |
| `tree` / `director` | array | 目录树文本行 |
| `log` / `timeline` / `change` | array | change_log 快照 |
| `artifact` / `output` | object | 产物文件名清单 |

标量映射（仅含义无歧义的计数/标识）：`total_files`、`file_count`、`files` →
文件数；`total_dirs` → 目录数；`total_bytes` → 总字节；`duplicate_count` →
重复文件数；`version_count` → 版本候选数；`case_id` → 案例标识。

## 4. 保留键与冲突

`result.json` 顶层保留键：`case_id`、`status`、`sources`、`uncertainty`、
`change_log`、`errors`、`degradations`、`runtime`、`limitations`、
`pending_human`、`computed`、`artifacts`。

- 契约字段名与保留键冲突时，改存 `computed._contract_<字段>`，并在
  `computed.contract_coverage.collisions` 记录映射。
- `case_id` 与 `status` 两个保留键直接满足契约同名要求，记入
  `contract_coverage.satisfied_by_reserved`。
- 其余契约字段原样写入顶层，不改名、不删除未知键。

## 5. 覆盖率报告

`computed.contract_coverage` 包含：

```json
{
  "total": 8, "filled": 8, "missing": 0, "null": 0,
  "collisions": [], "satisfied_by_reserved": ["case_id", "status"],
  "field_map": [{"path": "summary", "location": "top.summary", "type": "object"}]
}
```

`field_map` 记录每个契约叶子字段在 result.json 中的实际位置（`top.<path>` 或
`computed._contract_<字段>`），使 `--check` 与人工复核都能定位每个字段。

## 6. 六类要素落盘位置

| 要素 | 位置 |
|---|---|
| 文件类型 | `library_manifest.csv` 的 `format` 列；`computed.summary.by_extension` |
| 元数据字段 | `library_manifest.csv` 各列；`computed.catalogs`；`computed.index.metadata` |
| 目录层级 | `directory_tree.txt`；`computed.tree_lines`；空目录标 `[空目录]` |
| 重复文件 | `dedup_version.json` 的 `duplicate_groups`；`computed.duplicate_groups` |
| 版本差异 | `dedup_version.json` 的 `version_candidates`（chain + diffs，标 heuristic） |
| 权限与来源 | `library_manifest.csv` 的 `mode_octal/nlink/inode/readable/writable`；`computed.aliases` |

## 7. change_log 契约

每条：`{ "seq", "time_utc", "actor", "step", "detail", "contract_fields_updated" }`。
`actor ∈ {skill, agent}`；`step` 为 `skeleton/run/update`；追加式写入，并与
`operation_log.jsonl` 同步。`--update` 只接受 `set/merge/append` 三种幂等操作。
