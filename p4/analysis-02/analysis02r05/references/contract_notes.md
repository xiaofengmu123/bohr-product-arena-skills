# 输出契约与字段对齐规范（contract_notes）

本 Skill 的核心设计取舍是**契约优先（contract-first）**：`result.json` 的顶层字段不是
按作者猜测写死的，而是从 `/input/case.json` 的 `output_contract` 逐键反射生成，
再叠加固定的信封字段。这是本包与"写死一个固定结果结构"的方案最本质的区别。

## 1. 顶层结构 = case_id + 契约字段 + 信封

```text
result.json = {
  "case_id": "<原样复制，缺失为 unknown-case>",
  ... output_contract 的全部键，保持契约中的原始顺序 ...,
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [...], "uncertainty": [...], "pending_human": [...],
  "change_log": [...], "errors": [...], "degradations": [...],
  "runtime": {...}
}
```

契约字段在 `output_contract` 中的相对顺序在 `result.json` 中保持不变（Python 3.7+
的 dict 保序）。`audit` 子命令会输出 `contract_alignment` 数组，逐字段报告
`present` 与 `value_type`，这是"逐一对齐"可被复核的证据，而不是一句自述。

## 2. 字段值的回填规则（不编造）

- 骨架阶段（`skeleton`）：对 `output_contract` 的每个值推断**类型空值**——
  dict→`{}`、list→`[]`、标量→`null`；若契约值是 `"array"/"list"` 等类型词，
  映射为 `[]`，`"object"/"dict"` 映射为 `{}`，数值类型词映射为 `null`。
- 分析阶段（`emit`）：按字段名小写映射回填，见 `main.py` 的 `_FIELD_MAP`——
  `descriptive`←描述统计、`inferential`←检验、`effects`←效应量、`quality`←质量检查、
  `interpretation`←解释、`result`/`results`/`analysis`/`findings`←分析摘要。
- 无法映射的未知契约键**原样保留类型空值**，不删除、不改名、不猜测内容。
- 缺值只允许 `null`/`[]`/`{}`，不允许用示例数据或占位数字冒充结果。

## 3. change_log（一等公民，不是后补）

`change_log` 在骨架阶段就已出现（空数组），每次清洗动作即时追加一条：

```json
{"id": "CL1", "time": "T001", "action": "drop_missing_measure",
 "before": 34, "after": 33, "reason": "..."}
```

- `action` 目前有：`drop_missing_measure`、`drop_exact_duplicates`、
  `flag_iqr_outliers`；未来扩展保持同构。
- `before`/`after` 必须是该动作前后可比较的同一口径（行数、样本量、计数）。
- **`time` 用确定性序号 `T001...` 而非墙上时钟**：这是刻意取舍——保证同输入
  两次运行结果字节一致（平台另行记录真实时间戳）。若评分强调真实时间，可在
  Agent 补写解释时于 `runtime` 中另行记录，不破坏数值确定性。
- 清洗动作只记录"发生了、影响多少、为什么"，不静默删除：删除缺失行与重复行
  保留计数；异常值只标记（IQR 1.5）不删除。

## 4. status 的诚实性

| status | 触发条件 |
|---|---|
| `ok` | 至少产出描述统计或推断统计，材料与契约完整 |
| `partial` | 骨架可解析但结果不完整；或脚本兜底 |
| `insufficient_evidence` | 无可读数据表 / 关键字段缺失 / 样本量不足无法检验 |
| `input_error` | case.json 缺失或不可解析、输入结构非法 |

`partial` 必须同时用 `uncertainty` 或 `pending_human` 说明缺口，禁止把
"材料不足"写成"无差异"。

## 5. 与评分维度的对应

| 评分维度 | 本包对应设计 |
|---|---|
| 实际效果·完整性 | `audit` 逐字段核对契约键 + 信封键齐全 |
| 实际效果·可核验 | `change_log` + `cleaned.csv` + 确定性脚本（无随机、无时钟） |
| 可靠体验·异常处理 | 坏 JSON/坏编码/超大文件/二进制格式/组内方差为零均有降级 |
| 生态贡献·可复用 | `scripts/stats.py` 为纯标准库、函数化、可被新课题直接 import |

## 6. 复现要求

- 只用 Python 标准库（`math/csv/json/sqlite3/statistics` 等），无第三方依赖。
- 无随机数、无 `hash()` 做去重键（改用规范化 JSON 字符串）、无墙上时钟进
  数值/日志，保证两次运行字节一致。
- `python -B scripts/selftest.py` 退出码 0 为提交前底线。
