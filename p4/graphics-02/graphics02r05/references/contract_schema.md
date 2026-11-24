# 契约对齐字段规范

本文件定义 `sci-figure-contract` 如何把 `/input/case.json` 的 `output_contract` 逐字段对齐到 `/app/output/result.json`。

## 1. 归一化规则

| 输入 `output_contract` | 归一化结果 | `contract_source` |
|---|---|---|
| dict | 深拷贝原样使用 | `input:output_contract` |
| list-of-strings | `{field: null, ...}` | `input:output_contract(list)` |
| 缺失 / null | 题目三类交付字段兜底 | `default` |
| 其他类型 | 三类交付字段兜底 | `invalid` |

默认契约（仅兜底时使用）：

```json
{
  "editable_source": null,
  "exports": null,
  "source_and_change": null
}
```

## 2. 叶子遍历与字段路径

契约被深度优先遍历，叶子节点路径用 `.` 连接，列表下标用 `[i]`。例如：

```json
{"editable_source": {"svg": null, "pptx": null}, "exports": {"png": null}}
```

产生叶子路径：`output_contract.editable_source.svg`、`output_contract.editable_source.pptx`、`output_contract.exports.png`。空 dict/list 视为一个叶子，键名记 `_empty`。

## 3. 类型推断填充（resolved_kind）

按键名（小写、`-`/`_`/空格归一）按顺序匹配，先命中者生效：

| resolved_kind | 匹配键示例 | 填充值 |
|---|---|---|
| change_log | change_log / history / revision / 修改记录 | `{ref, count, steps}`（引用顶层，不重复存） |
| source_and_change | source_and_change / 来源与修改 | `{tables, sources, change_log_ref}` |
| evidence | evidence / trace / provenance | `evidence` 列表（locator+quote） |
| mapping | mapping / 映射 | 图↔数据双向映射对象 |
| pptx | pptx / ppt / slides | office 产物路径或 null |
| eps | eps | null（SVG/PDF 已覆盖） |
| png | png / bitmap / raster | PNG 路径或 null |
| pdf | pdf | PDF 路径或 null |
| svg | svg / editable_source / source_file / vector | SVG 路径或 null |
| exports | exports / render / preview | `{svg,png,pdf,pptx}` 已有子集或 null |
| table_data | table / data / csv / series | 表格摘要 |
| sources | sources / 来源 | 来源清单 |
| case_id | case_id | case_id |
| status | status | null（顶层统一管理） |
| unknown | 其他 | null + pending_human |

## 4. fill_status 语义

- `filled`：叶子被填为标量路径/字符串。
- `object`：叶子被填为结构化对象（exports / evidence / source_and_change 等）。
- `null`：无法提供，值 `null`，进 `contract_declined`，status 相应降级。
- `preserved`：叶子原始值非空（非 null/""/[]/{}），原样保留。

## 5. 平铺规则（顶层镜像）

仅当契约是**单层 dict** 且字段名**不在保留键集合**内，才把字段平铺到 result.json 顶层：

保留键：`case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、`change_log`、`evidence`、`mapping`、`artifacts`、`runtime`、`contract_alignment`、`output_contract`、`determinism`、`self_check`、`contract_source`、`contract_fields`、`contract_declined`、`evidence_map`。

嵌套契约不平铺，全部字段只在 `output_contract` 内保留（结构逐层对齐，避免层级错位）。

## 6. change_log 与审计的对应

- 每个叶子的对齐动作追加一条 `change_log`：`step=S5_contract_align`、`status=ok|partial`、`note="<path> -> <fill_status>: <reason>"`。
- `contract_alignment[].change_log_index` 指向该条记录的索引，实现“字段—审计—修改记录”三方可核验。
- 最终 `status`：存在 `contract_declined` 或 `errors` 非空 → `partial`；否则且至少有产物 → `complete`。

## 7. 诚实约束

- 不伪造 PNG/PDF：脚本只写 SVG；PNG/PDF 仅在运行时用 scientific-python/office-renderer 真实渲染后出现，`align` 自动扫描并回填。
- 契约缺失/无法解析时，`contract_source` 与 `uncertainty` 明确记录，不用示例填充。
- 保留原始值不覆盖；无法判断的数值保留原值并标注，不擅自改数。
