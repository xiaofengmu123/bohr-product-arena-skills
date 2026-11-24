# 审查口径与判定规则（review rubric）

本文档定义七类约束的判定标准、状态枚举、算术规则与诚实标注规则。`result.json` 的每个审查项都应能对照本文档独立复核。

## 1. 判定枚举

| verdict | 含义 | 输出要求 |
|---|---|---|
| `ok` | 方案已写且与合同/参数域/样本一致，无未解决缺口 | 必须带 evidence |
| `conditional` | 有条件可行 | 必须写 `conditions`（逐条可执行） |
| `blocked` | 缺关键资源/样本/审批，无法开工 | 必须写 `blockers`（缺什么） |
| `unconfirmed` | 信息在材料中缺失且须他人确认 | 必须写 `needs_expert` 或 `action.owner/ask` |
| `missing` | 方案该写而未写 | 说明缺哪一项、影响哪个判读 |
| `conflict` | 同指标两个来源不一致 | 两个证据都记录，至少一个 `role="contradicts"` |

`resource_state`（仅 `equipment_materials`）：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。判定必须来自合同/清单，不写“建议采购”代替事实。

`check_basis`：`plan_stated`（方案原文）、`plan_silent`（方案未写但合同/域已给）、`contract`（设备/材料/预约/报价章节）、`domain`（参数域）、`computed`（算术推导）、`source`（外部文献/SOP）。

## 2. 七类约束的最小检查项

### 2.1 assumptions_variables（假设与变量）
- 研究问题是否可检验（自变量、因变量、混杂因素可识别）。
- 每个假设是否有依据（文献/SOP/预实验）；依据缺失记 `unconfirmed`。
- 变量是否可测量、单位是否明确、读数是否可复核。

### 2.2 controls（对照组）
- 是否设阴性对照、阳性对照、空白对照、质控/标准品、批次对照。
- 缺少必要对照 → `missing`，不得以“常见做法”默认满足。

### 2.3 equipment_materials（设备与材料）
- 设备型号/精度/附件是否与方案声称匹配；状态是否来自清单。
- 耗材规格、货期、批次一致性是否写明；未写明记 `missing`。
- 状态判定四选一，写清依据 locator。

### 2.4 time_cost（时间成本）
- 按步骤拆设备占用、人工操作、等待、检测周转，逐项给算式。
- 单位归一（见第 4 节）后累加；未写明的等待不得按 0 计算。
- 核对 deadline、预约可用日与材料货期；冲突记 `conflict`。

### 2.5 parameter_ranges（参数范围）
- 每个参数与 `parameter_domains` 的 `min/max/step` 比对：`min <= value <= max`。
- 越界记 `conflict`；域未给记 `missing`；步骤容差未写记 `unconfirmed`。

### 2.6 risks_exceptions（风险与异常）
- 覆盖设备故障、材料批次差异、样本不足、参数越界、数据质量不合格。
- 每个风险给出降级路径、替代方案、转人工节点；缺处置记 `missing`。

### 2.7 ethics_safety（伦理与安全）
- 是否涉及动物/人体样本/危险试剂/个人数据 → 对应审批层级、材料、大致耗时。
- 涉及而未写审批路径 → `unconfirmed` + `needs_expert=true`，不得写成已通过。

## 3. 诚实标注规则

1. **“方案没写” ≠ “方案满足”**：未写即 `missing`，不得默认满足。
2. **“已在别处出现” ≠ “在本方案成立”**：合同有的设备，若方案未列明使用条件，仍需核对。
3. **冲突不隐藏**：两来源不一致时两条都记录，`verdict="conflict"` 或 `notes` 注明 `source_conflict`。
4. **未经审批不写事实**：伦理/安全/设备可用性未确认 → `unconfirmed`。
5. **不虚构**：不编 DOI、数值、价格、审批状态、预约记录；缺失写 `pending_human`/`missing`。

## 4. 时间与单位归一表

| 单位 | 归一为分钟（乘数） |
|---|---|
| s / sec / 秒 | 1/60 |
| min / 分钟 | 1 |
| h / hr / 小时 | 60 |
| d / day / 天 | 1440 |
| week / 周 | 10080 |

质量/体积单位：µL→1、mL→1000、L→1e6（µL 基准）；ng→1、µg→1000、mg→1e6、g→1e9。成本按币种分别求和，不跨币种折算（除非材料给汇率）。样本量用 `available - required >= 0` 判足量，并注明是否含复孔/损耗/分组倍数。

## 5. 版本比较规则

- 先由脚本对齐字段差异（`plan_diff`），再对每处差异判“是否影响可行性”。
- 排序口径：`blocking`、`unresolved`、参数越界数、总时长，升序；仅为算术参考。
- 推荐版本必须说明依据，且不能掩盖未量化的可行性因素；无把握写 `recommended=null` 并说明待人工确认。

## 6. 置信度与降级

- `confidence`：`high`（有合同/域/复算支撑）、`medium`（单一来源或部分信息）、`low`（推断或记忆，须复核）。
- 空输入 → `insufficient_evidence`；坏 JSON/坏编码 → `input_error` 或记入 `degradations` 继续处理；PDF 不可读 → `degradations` 标注未读。
- 写盘失败 → 降级最小合法 JSON，进程退出码 0。
