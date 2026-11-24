# result.json 输出契约与证据规范

本文件是 `SKILL.md` 第 3 节输出契约的精确定义，与 `scripts/core_impl.py` 中
`TOP_KEYS / INTERPRETATION_KEYS / PLAN_KEYS / RISK_KEYS / CONFIRM_KEYS` 保持一致。
`result.json` 是运行时产物，Skill 包内不得预置正式案例答案。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 来自 `case.json` 的 `case_id/id/challenge_id/armiId` 或 `task.md` 的 `challenge_id`；解析失败用输入目录名兜底 |
| `status` | string | 是 | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`；必须诚实 |
| `task_type` | string | 否 | 任务类型简述，未知写 `null` |
| `summary` | string | 否 | 1–3 句平实结论，不夸大、不编造 |
| `interpretation` | object | 是 | 五段结论，见第 2 节 |
| `experiment_plan` | object | 是 | 假设/变量/对照/步骤/参数/资源/判读标准，见第 3 节 |
| `evidence_and_risk` | object | 是 | 依据/风险与异常处置/假设，见第 4 节 |
| `confirmation_items` | object | 是 | 安全/伦理/设备可用性/专家确认，见第 5 节 |
| `sources` | array | 是 | 材料来源清单，含 `rel / size_bytes / sha256 / role` |
| `uncertainty` | array | 是 | 单位、方法、阈值歧义逐条记录，不擅自统一 |
| `change_log` | array | 是 | 骨架→画像→结论→定稿各阶段记录，含 `phase / action / ts / note` |
| `errors` | array | 是 | 坏 JSON、读文件失败等运行错误 |
| `degradations` | array | 是 | 坏编码降级、超限跳过等降级记录 |
| `limitations` | array | 是 | 对照缺失、重复数不足、材料缺失等限制 |
| `pending_human` | array | 是 | 未经专家确认、不能写成事实的事项 |
| `runtime` | object | 是 | `start_ts / end_ts / elapsed_s / requests_used / requests_total / seconds_budget` |

若 `case.json` 提供 `output_contract`（含 `fields/required/schema` 或扁平键），必须
**原样回填其全部键**：缺值给 `null`/`[]`，不改名、不删未知键。回填逻辑见
`core_impl.merge_output_contract`。

## 2. `interpretation` 五段结论

每段是数组；未知/无材料时写空数组 `[]` 并到 `limitations` 说明，不填充示例。

- `trends`：方向（增/减/稳定/非单调/有拐点）、所依据区间、对照、拟合方式（线性/
  滚动均值/手动目测）、幅度与单位。禁止把“读数复述”当趋势。
- `anomalies`：定位到样本/批次/通道/孔位/时间点；给出观察值、期望值、偏离幅度、
  判定阈值（写明 z-score/IQR/MAD/Grubbs/手动及数值）、是否超阈值、与对照/重复的偏离。
- `possible_causes`：按证据强度/可检验性排序；每条写 `supporting_evidence`（支持）、
  `excluding_evidence`（排除）、`confound_risk`（相关≠因果）、`testability`。无排除
  过程的机理猜测只能标低置信并写明缺哪步验证。
- `verification_steps`：第二个人可直接执行的核查动作；每条写 `target`、
  `expected_if_supported`、`decision_rule`。禁止“检查数据质量”这类不可执行表述。
- `followup_experiments`：对应原因候选与证据缺口写具体改动（补对照、加密采样、调
  参数范围、扩重复数），并写 `reason` 与 `expected_outcome`。

## 3. `experiment_plan`

- `hypothesis`：可证伪假设
- `testable_question`：可检验问题
- `variables`：自变量/因变量/控制变量
- `controls`：对照设置（阳性/阴性/空白/平行重复）
- `steps`：可执行步骤
- `parameters`：参数与范围
- `resources`：设备/材料/时间/成本
- `interpretation_criteria`：判读标准与阈值

## 4. `evidence_and_risk`

- `evidence_basis`：文献/设备/历史记录依据
- `risk_and_anomaly_handling`：风险与异常处置
- `assumptions`：假设与边界

## 5. `confirmation_items`

- `safety`、`ethics`、`equipment_availability`、`expert_confirmation`
- 未经确认的建议不得写成可执行事实，进 `pending_human`。

## 6. 证据溯源（硬要求）

- 每条关键结论/数值/图元绑定 `evidence` 数组，每项含 `locator`（如
  `materials/paper.md#p3`、`data/batch_12.csv#L34`）与 `quote`（逐字摘录）。
- 同一指标两来源冲突时两条都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
- `status=ok` 时，`validate` 要求每个非空结论项具备 locator+quote；缺失会报 ISSUE。

## 7. 确定性

固定排序（文件按相对路径排序、列按出现顺序、离群行号升序）、无随机数；
同一输入两次运行结果一致。
