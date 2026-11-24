# 输出契约对齐规范（output_contract）

本 Skill 的变体重点是**契约完整性**：`result.json` 必须逐字段覆盖 `/input/case.json` 的 `output_contract`，并在 `change_log` 留下可审计的填充记录。

## 1. 契约发现

按优先级递归查找 `case.json`：

1. 顶层 `output_contract`（dict 或 list）。
2. 任意嵌套层级中的第一个 `output_contract`。
3. 都找不到 → 用默认三键并记录 `degradations`：

```json
{"analysis_results": null, "quality_evidence": null, "anomaly_notes": null}
```

`case_id` 按 `case_id → challenge_id → armiId → id` 取值，仍缺失则用输入目录名。

## 2. 语义映射表

对每个契约字段名做小写包含匹配（先到先得），未命中则保留键名、值给 `null`（标量）或 `[]`（spec 含 list/array 提示）：

| 字段名包含 | 填充值 |
|---|---|
| `analysis_result` / `analysis` / `main_result` / `result_summary` | 完整 `stats.json` |
| `statistic` / `stats` / `test` | 完整 `stats.json` |
| `quality` / `reproduc` / `evidence` / `proof` | `clean_meta.json` |
| `anomal` / `exception` / `issue` / `error_note` | 异常说明列表 |
| `report` | `report.md`（相对路径，不存在则 null） |
| `figure` / `chart` / `plot` / `visual` / `diagram` / `graph` | `figure_*.svg` 列表 |
| `clean` / `table` / `csv` / `data_file` | `cleaned.csv` 相对路径 |
| 其他 | `null` 或 `[]`，写入 `degradations` + `pending_human` |

## 3. 铁律

- **不改名**：契约键叫什么，result.json 顶层就叫什么。
- **不删除未知键**：未命中语义映射的键也必须在顶层出现。
- **可审计**：`change_log` 对每个字段记 `{step:"contract_fill", field, spec, filled, action}`；`filled=false` 的键必须同时出现在 `degradations`。
- **可回查**：`_output_contract_source` 保存契约原文。
- **完整性可机检**：`scripts/selftest.py` 逐键比对，缺键即 FAIL。

## 4. 最小示意（示例，非正式案例）

输入 `case.json`：

```json
{"case_id":"demo", "output_contract":{"analysis_results":"...","quality_evidence":"...","anomaly_notes":"..."}}
```

`result.json` 顶层必须含 `case_id`、`analysis_results`、`quality_evidence`、`anomaly_notes`，外加 `status/sources/uncertainty/change_log/...`。缺失材料时诚实返回 `insufficient_evidence`，不伪造契约字段内容。
