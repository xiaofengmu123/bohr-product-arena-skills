# 字段级契约与状态语义

本文档定义 `result.json` 与三个产物的字段语义、定位规范与状态取值，供实现与复核对照。

## 1. result.json 顶层字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string | 从输入 `case.json` 原样带回（优先 `case_id`/`id`/`case`），不允许改写 |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error`，见下方状态语义 |
| 契约字段 | any | `case.json.output_contract` 的全部键，逐键回填；缺值 `null`/`[]`，不改名不删未知键 |
| `result` | object | 盘点摘要 + `files` + `analysis` + `index`，主结果载体 |
| `claims` | list | 逐条结论，每条含 `claim_id`/`kind`/`confidence`/`text`/`evidence` |
| `sources` | list | 来源清单，每条含 `source_id`/`locator`/`kind`/`sha256`/`size`/`source` |
| `uncertainty` | object | `{summary, count, items}`，items 为 `uncertainties` 的聚合 |
| `uncertainties` | list | 逐条不确定性与冲突 |
| `change_log` | list | 操作记录，每条含 `op`/`target`/`before`/`after`/`reason`/`locator` |
| `pending_human` | list | 需人工确认的缺口 |
| `errors` | list | 可恢复错误（退出码仍为 0） |
| `degradations` | list | 降级说明（如 `no_draft`、`output_fallback`、`no_tool:*`） |
| `runtime` | object | 仅放确定性/耗时/契约计数，**不含时间戳**以保证重复运行一致 |

## 2. 状态语义

| 值 | 含义 |
|---|---|
| `ok` | 材料齐全、盘点与分析完整、检索问题已回答或明确无直接命中，无明显冲突 |
| `partial` | 已交付可复核的部分结果；存在未裁定冲突、缺 draft、或部分材料不可读 |
| `insufficient_evidence` | 材料不足或无文本层，无法得出可靠结论 |
| `input_error` | `case.json` 缺失或坏 JSON；仍盘点材料并交付合法骨架 |

## 3. 证据定位规范

- `source_id`：`F001`、`F002`…（`library.json.files[].id` 稳定编号），或 `case`（指 `case.json`）。
- `locator`：相对输入目录的 POSIX 路径（如 `materials/report_final.md`），不回退为绝对路径。
- `quote`：材料中的**逐字片段**。文本文件要求与材料完全一致（含空格与标点）；二进制/扫描件用 `quote_verbatim=false` 并注明 `binary_material_locator_only`。
- 每个关键结论必须能通过 `source_id + locator + quote` 指回材料；冲突信息同时记录两侧来源并标 `source_conflict`。

## 4. evidence_map.json 结构

顶层为 claim_id（如 `C001`、`N001`、`query:Q1`）到证据数组的映射：

```json
{
  "C001": [
    {"source_id": "F001", "locator": "materials/a.md",
     "quote": "逐字片段", "quote_verbatim": true}
  ],
  "query:Q1": [
    {"source_id": "F003", "locator": "materials/b.txt",
     "quote": "……", "quote_verbatim": true}
  ]
}
```

二进制材料证据的 `quote_verbatim` 为 `false`，`quote_note` 说明只能按 locator 核对。

## 5. 确定性与可复跑

- 文件按相对路径排序后稳定编号，哈希为 sha256（流式，超限跳过）。
- 归一化 stem 只移除版本/日期/副本噪声标记，不引入时间戳或随机数。
- `result.json`/`library_index.json`/`evidence_map.json`/`inventory.md` 同输入两次运行除 `runtime` 外逐字节一致。
