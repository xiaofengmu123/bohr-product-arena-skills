# 输出契约：plan.json 与 result.json 字段定义

本文件是 Skill 运行时的数据契约。工作稿 `/app/work/plan.json` 是唯一事实源（single source of truth），`finalize` 把它合并为 `/app/output/result.json`，并渲染 `plan.md`、复制 `plan.json` 到输出目录。

## 1. status 语义

| 值 | 含义 | 触发条件（示例） |
|---|---|---|
| `ok` | 方案完整且通过校验 | 四项全覆盖、参数在可行域内、来源可溯源、验证无阻断错误 |
| `partial` | 已落盘但方案不完整 | 部分参数/响应/成本缺失，或仍在增量改进 |
| `insufficient_evidence` | 材料不足，无法给出可执行方案 | 无材料、无 case.json、关键约束/响应无法确定 |
| `input_error` | 输入损坏，无法处理 | case.json 坏 JSON 且无任何可读材料，全部不可读 |

## 2. plan.json（工作稿）顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`；未知填 `""` |
| `status` | string | 见上表 |
| `title` | string | 方案标题 |
| `research_question` | string | 由研究目标转成的可检验问题 |
| `objective` | string | 优化目标（含方向与优先级） |
| `assumptions` | list[str] | 明确假设，每条可被质疑 |
| `variables` | list[object] | 自变量/因变量/混淆变量，各含 name/role/unit/description |
| `parameters` | list[object] | 关键参数，结构见 §3 |
| `controls` | list[object] | 对照设计（空白/阳性/阴性/历史对照），含 name/purpose/spec |
| `procedures` | list[object] | 步骤，含 step/action/expected_output/acceptance |
| `resources` | list[object] | 设备/材料/软件/人员，含 name/spec/quantity/source |
| `response_metrics` | list[object] | 响应指标，结构见 §4 |
| `interpretation_criteria` | list[object] | 判读标准，含 criterion/threshold/action |
| `search_strategy` | object | 结构见 §5 |
| `cost_budget` | object | 结构见 §6 |
| `basis` | list[object] | 方案依据，含 claim/source_id/summary |
| `risks` | list[object] | 风险，含 risk/likelihood/impact/mitigation |
| `exception_handling` | list[object] | 异常处置，含 scenario/detection/response |
| `confirmations` | list[object] | 确认项，含 item/owner/status(confirmed|pending) |
| `sources` | list[object] | 证据映射，结构见 §7 |
| `uncertainty` | object | 结构见 §8 |
| `limitations` | list[str] | 已知局限，如实写 |
| `pending_human` | list[str] | 需人工/专家确认项 |
| `change_log` | list[object] | `{time, action, reason}`，按时间递增 |
| `errors` | list[str] | 脚本运行/输入错误记录 |
| `degradations` | list[str] | 降级记录（跳过、缺软件、缺数据等） |

## 3. parameter 对象

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 参数名（含符号） |
| `unit` | 是 | 单位 |
| `recommended_value` | 是 | 建议值（数值或明确字符串） |
| `feasible_range` | 是 | `{min, max}`，数值或带单位的字符串 |
| `step` | 是（网格搜索） | 数值步进 |
| `constraint_source` | 是 | 约束来源定位（如 `materials/manual.pdf#p3`） |
| `quote` | 是 | 约束逐字引用 |
| `basis` | 否 | 取值依据说明 |

## 4. response_metric 对象

| 字段 | 说明 |
|---|---|
| `name` | 指标名 |
| `unit` | 单位 |
| `measurement_method` | 测量方法/仪器 |
| `direction` | `maximize` / `minimize` / `target` |
| `target` | 目标值或区间 |
| `acceptable_lower_bound` | 可接受下限（对 maximize 类）或等价阈值 |
| `prediction_method` | 历史拟合 / 代理模型 / 经验规则 |
| `uncertainty` | 预测不确定度披露 |

## 5. search_strategy 对象

| 字段 | 说明 |
|---|---|
| `method` | `grid` / `sequential` / `bayes` / `screening` |
| `initial_points` | 初始点列表 |
| `per_round` | 每轮补点规则 |
| `stop_criteria` | 收敛与停止判据 |
| `candidate_count` | 候选点数（正整数，默认 8） |
| `seed` | 随机种子（默认 0，保证可复现） |

## 6. cost_budget 对象

| 字段 | 说明 |
|---|---|
| `items` | 明细 `{item, quantity, unit_cost, cost, basis}` |
| `total` | 总成本（含口径说明） |
| `time_hours` | 总机时/工时 |
| `tradeoff_note` | 多目标取舍说明：牺牲多少响应可接受 |

## 7. source 对象（证据溯源）

| 字段 | 说明 |
|---|---|
| `id` | 唯一标识 |
| `locator` | 材料位置定位，如 `materials/paper.md#p3` 或 `case.json#constraints` |
| `quote` | 逐字引用（不得改写） |
| `claim` | 该证据支撑的结论/数值 |
| `conflict` | 可选：与其他来源冲突时标 `source_conflict` |

每个关键结论/数值/约束都必须至少映射到一条 source；冲突不隐藏，两条都记并标 `source_conflict`。

## 8. uncertainty 对象

| 字段 | 说明 |
|---|---|
| `unverified_values` | 未经验证的数值列表 |
| `missing_data` | 缺失数据清单 |
| `assumptions_flagged` | 已标注为假设的内容 |
| `prediction_uncertainty` | 预测不确定度的总体说明 |

## 9. result.json（最终产物）顶层字段

`finalize` 生成的结构：`case_id`、`status`、`plan`（§2 中除 meta 之外的内容键）、`artifacts`（`plan.md` 与 `plan.json` 的 path/type/description/absolute_path）、`sources`、`uncertainty`、`limitations`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`（`started_at`/`finished_at`/`skill`）。

- `artifacts` 必须真实存在且可打开；路径与 `/app/output/` 下文件一致。
- 所有未知键保留、不改名；缺值用 `null`/`[]`，不删除。
- 不编造 DOI、数值、引用；缺失写 `pending_human` 或 `not_reported`。
