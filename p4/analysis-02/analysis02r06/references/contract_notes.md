# 契约、中间表示与评分维度映射（contract_notes）

本文说明 result.json 的字段级契约、九个中间产物的规范，以及它们如何对应评分维度。
Agent 在 `build` 前读本文，确保逐键回填、不删未知键、不编造。

## 1. result.json 字段级契约

- 顶层必须包含 `case_id`（从 `/input/case.json` 原样复制，缺失写 `"unknown-case"`）。
- `output_contract` 的**每一个键**都要回填：`dict` 缺 `{}`、`list` 缺 `[]`、标量缺
  `null`；不改名、不删未知键、不新增会覆盖契约的别名。
- 信封字段：
  - `status` ∈ `ok | partial | insufficient_evidence | input_error`，诚实取值。
  - `sources[]`：`{source_id, path, kind, rel_path}`，指向真实存在的输入文件。
  - `uncertainty[]`：列缺口与不确定项，逐条具体，不写空话。
  - `change_log[]`：`{id, time, action, before, after, reason}`，记录每次增量改进。
  - `errors[]`/`degradations[]`/`pending_human[]`：诊断与待人工确认项。
  - `runtime{}`：skill 版本、起止时间；不虚构 benchmark 分数或软件状态。

## 2. 九个中间产物的规范（本版设计取舍）

中间产物写 `/app/output/intermediate/`，全部 UTF-8 JSON；数值用**全精度**（json 最短
往返表示），仅报告层做显示舍入。复核流程：重跑 `analyze` → 比较 `stat_model.json` 的
hash（`run_log.json`）→ 数值一致即复现。

| 产物 | 关键字段 | 何时写 |
|---|---|---|
| `index.json` | files[{path,rel_path,size,kind}] | init |
| `survey.json` | files、tables、tools | survey |
| `data_model.json` | tables[{name,columns[{name,type,missing}]}] | inspect |
| `plan.json` | design、hypotheses、tests_planned、groups、outcome | init(骨架)→④补全 |
| `cleaning_model.json` | tables[{n_before,n_after,rules[]}] | analyze |
| `stat_model.json` | tables[{descriptive,normality,inferential,correlation}] | analyze |
| `figure_model.json` | figures[{id,type,variable,group_by,path}] | analyze |
| `evidence.json` | claims[{claim,locator,quote,source_kind}] | analyze |
| `run_log.json` | stat_model_hash、version、budget | selfcheck |

`plan.json` 是唯一需要 Agent 语义补写的中间产物：读 task_instruction 后确定设计
（两组/三组/配对/相关）、分组变量、结局变量、单位与假设，再回写。其余产物由脚本
确定性生成，Agent 不手工改动数值，只补解释。

## 3. 复现要求

- 脚本只用标准库（`math/csv/json/sqlite3/zipfile/xml`），无 numpy/scipy 依赖。
- 无随机数：所有抽样、排名、图布局均确定性；同输入两次运行 hash 一致。
- `analysis_code.py` 与 `scripts/*.py` 同内容，落在 /app/output，可独立复跑。
- 证据溯源：关键结论的 `evidence.json` 条目带 `locator`（文件+列/行）与 `quote`
  （原文/计算式），冲突信息两个都记并标 `source_conflict`。

## 4. 降级规则

| 场景 | 动作 | 状态 |
|---|---|---|
| 无数据表 | 写空中间产物 + uncertainty 列缺口 | insufficient_evidence |
| case.json 坏/缺失 | 记 errors/degradations，case_id 用 unknown-case | partial / input_error |
| 单文件 >2MB | 跳过并记 degradation | 视其余材料 |
| 某列全缺失/方差为 0 | 该列描述给 n，检验标不适用 | partial |
| 单位不明 | `unit_unknown`，不猜 | 不影响 status |
| scipy 缺失 | 用脚本内置算法，口径照写 | ok |
| 脚本不可运行 | 手写合法 result.json（SKILL.md §8） | partial |

## 5. 评分维度映射（供自检对照）

| 评分维度 | 分值 | 本包对应物 |
|---|---|---|
| 实际效果·任务完成度/正确性 | 40 | stat_model.json 全精度统计量、检验选择表、效应量+CI |
| 使用增益·质量/效率 | 25 | 确定性脚本替代手算；中间产物免重算；Agent 只补解释 |
| 创新增益·新增能力/方法改进 | 15 | 中间表示层：plan/data_model/stat_model/figure_model/evidence |
| 可靠体验·调用/稳定/异常 | 10 | main.py 子命令；init 先落骨架；降级不崩溃；空输入 exit 0 |
| 生态贡献·文档/复用 | 10 | SKILL.md + 两个 references；脚本可复跑；无预置答案 |

## 6. 交付前最后核对

1. `python scripts/statlib.py` 与 `python scripts/main.py selfcheck` 均退出码 0。
2. `result.json` 用 `json.load` 可解析；case_id 与 case.json 一致。
3. `output_contract` 每个键都有值（至少 null/[]），未知键未被删除。
4. `stat_model.json` 的数值全精度、与 cleaned CSV 一致、与报告一致。
5. 无任何"证明有效"式措辞；p 写精确值；CI 不误读为个体范围。
