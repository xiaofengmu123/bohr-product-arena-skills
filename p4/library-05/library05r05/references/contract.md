# 字段级输出契约与状态语义

本文件定义 `result.json` 的字段契约、定位规范与状态语义，供实现与复核一致引用。

## 1. result.json 顶层结构

- `case_id`（字符串）：从 `/input/case.json` 原样带回，不得改写；缺失时为 `"unknown"`。
- 契约字段：`output_contract` 的全部字段，按原顺序逐键出现，缺值 `null`/`[]`，
  不改名、不删除、不合并未知键。契约发现顺序：dict 形态 > list 形态 > 默认契约。
- 信封字段：`status`、`result`、`claims`、`uncertainties`、`sources`、`uncertainty`、
  `change_log`、`pending_human`、`errors`、`degradations`、`runtime`、`contract_audit`。

## 2. 定位规范（locator）

- 文件定位用 `rel_path`（相对 `/input` 的路径，用 `/` 分隔）加 `sha256`；
  不写本机绝对路径。
- `case.json` 定位为 `case.json`，`task.md` 定位为 `task.md`。
- 每条 claim/note/query 的 evidence 必须是 `{source_id, locator, quote}`，
  `quote` 为材料中可逐字命中的片段；`quote == locator` 用于哈希/结构类结论的定位证据。

## 3. 状态语义

| status | 含义 |
|---|---|
| `ok` | 契约字段全部填充、draft 已写、无错误 |
| `partial` | 部分契约字段为 null、或 no_draft、或存在降级 |
| `insufficient_evidence` | 无可用材料/无文本层/空输入 |
| `input_error` | 坏 JSON/缺 case.json 等输入不可解析 |

`status` 必须与 `errors`/`degradations`/`pending_human` 自洽，不把未完成写成完成。

## 4. change_log 语义

每个条目含 `seq`（单调序号，非时间戳）、`scope`（如 `contract_field:xxx`）、
`before`、`after`、`reason`、`locator`、`status`（`applied | pending`）。
`init` 与 `build` 对每个契约字段各记一条，保证"谁改了什么、为什么"可复核。

## 5. contract_audit 语义

`contract_audit` 逐字段给出 `field / present / populated / type / source / change_seq`；
`source` 为 `computed:<键>` 或 `pending`。它是"输出契约是否对齐"的机器可核对证据。

## 6. 确定性要求

同一输入两次运行，除 `runtime` 外所有产物逐字节一致。禁止把时间戳、随机量、
遍历顺序等非确定信息写入确定性产物；文件与目录一律排序后写入。
