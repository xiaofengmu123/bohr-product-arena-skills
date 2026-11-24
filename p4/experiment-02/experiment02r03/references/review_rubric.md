# 审查口径与判定规则（review_rubric）

本文件是 `scripts/core_impl.py` 的判定口径说明，供人工复核与二次实现使用。所有阈值均为显式数值，避免"感觉一个数"。

## 1. 状态枚举

### result.status

| 值 | 含义 |
|---|---|
| `ok` | 七类检查均有产出，且无阻断性错误、无缺失关键信息，证据核验通过 |
| `partial` | 有可用审查结果，但存在 blocked/conflict、missing、unconfirmed 之一 |
| `insufficient_evidence` | 无 `case.json` 主输入或几乎无材料，无法审查 |
| `input_error` | `case.json` 存在但 JSON 解析失败 |

### overall_conclusion.verdict

| 值 | 判定 |
|---|---|
| `feasible` | 无 blocked/conflict，无 missing，无 blocking 审批项 |
| `conditionally_feasible` | 无 blocked/conflict，但有 missing/unconfirmed，满足条件后可开工 |
| `not_feasible` | 存在 blocked/conflict，或存在 blocking 审批项 |
| `undetermined` | 材料不足，无法形成结论 |

### 审查项 verdict

| 值 | 含义 | 证据要求 |
|---|---|---|
| `ok` | 材料证明该项满足 | 必须 evidence |
| `conditional` | 满足所列条件后可满足 | 必须 evidence + conditions |
| `blocked` | 材料证明该项不可行（参数越界/规格不足/样本不足） | 必须 evidence |
| `conflict` | 两个来源对同一指标不一致 | 必须 evidence |
| `unconfirmed` | 信息不足无法判定（未写 ≠ 未满足，也 ≠ 不具备） | conditions 说明缺什么 |
| `missing` | 方案完全未写该项 | conditions 说明需补什么 |

## 2. 资源状态（equipment_materials）

| 状态 | 判定 |
|---|---|
| `已具备` | 清单有同名条目，且规格比较 `spec_fit=True` |
| `可外协` | 规格不足但存在 vendor/外协/预约条目 |
| `需采购` | 存在报价或货期条目（`unit_price`/`lead_days`） |
| `不具备` | 有清单、规格明确不足、且无外协/采购来源 |
| `未知` | 未命中任何清单/合同，或规格不可比（清单可能不完整） |

**规格比较**：`spec_fit(required, available)` 解析数字+单位+上下限（`max/≤/up to`、`min/≥/at least`）；温度支持 °C↔K 换算，体积/质量支持 µL↔mL、µg↔mg 换算；无可比数字返回 `None` → `未知` + `needs_expert`。

## 3. 算术规则（全部确定性，用标准库实现）

### 时长
- `elapsed_h = Σ hands_on_h + Σ wait_h`（步骤串行假设；并行步骤需在步骤里显式标注 `parallel`，脚本按串行上界记录）。
- 单位换算：s/3600、min/60、h×1、d×24、wk×168；`overnight` 按 16h 计（显式假设，写入 `basis`）。

### 时间裕度（排期）
- `slack_h = available_hours - elapsed_h`：`≥20%` ok；`0–20%` conditional；`<0` blocked。
- 日历排期（仅当 `start_date` 与 `deadline` 都给，不用系统当前时间以保证确定性）：`net_days = (deadline-start).days - ceil(elapsed_h/24)`；`≥7` ok；`0–7` conditional；`<0` blocked。

### 设备机时
- `utilization = Σ step_duration / booking_hours`；`≤70%` ok；`70–100%` conditional；`>100%` blocked。

### 预算
- `slack = budget - Σ(ceil(need/pack)×unit_price)`；`≥10%` ok；`0–10%` conditional；`<0` blocked。
- 多币种 → `conflict`（不擅自兑换）。

### 样本
- `n_required = per_group_n × groups`；`n_consumed = n_required × replicates`。
- `margin = (available - n_consumed)/n_consumed`；`≥15%` ok；`0–15%` conditional；`<0` blocked。
- `available` 优先取方案内字段，否则取唯一样本合同的 `available`；多个样本来源时 `available=None` → `unconfirmed`。

### 参数
- 值域 `[min,max]`：域内且距边界 `>5%×span` → ok；域内但 `≤5%` → conditional；域外 → blocked。
- 枚举 `values`：命中 → ok；未命中 → blocked（附最近值与差值）。
- 无域 → `missing`。

### 货期
- 有 `lead_days_max` 与 `deadline` 但无 `start_date` → `unconfirmed`（无法算下单到首次使用的松弛，需提供日期）。

## 4. 严重度与方案排序

严重度 `conflict(5) > blocked(4) > missing(3) > unconfirmed(2) > conditional(1) > ok(0)`；`blocking=true` 的 `unconfirmed`（伦理）参与阻断计数。

方案排序键（字典序，确定性、无平局歧义）：

```text
(conflict+blocked, missing, conditional, cost, elapsed_h, version_label.lower())
```

`recommended_plan` 为相对最优，不代表可直接开工；若仍有阻断须先解除。

## 5. 伦理与安全映射

关键词扫描（英文不区分大小写）：

| flag | 命中词（节选） | 对应审批 |
|---|---|---|
| human | 人体/人源/受试者/血清/血浆/PBMC/human/blood/IRB/知情同意 | IRB/伦理委员会 |
| animal | 动物/小鼠/大鼠/实验动物/mouse/mice/IACUC | IACUC/动物伦理 |
| biohazard | 病原/致病菌/病毒/BSL-2/BSL-3/pathogen/菌株 | 生物安全委员会 |
| chemical | 剧毒/致癌/易燃/放射性/放射源/同位素/radioactive | 安全/辐射/危化品审批 |
| data | 个人数据/隐私/基因数据/privacy/PIPL/GDPR | 数据合规/隐私审查 |

批件状态判定：`approved/已批/通过/有效` → conditional（仍需核对覆盖范围与有效期）；`pending/待审/在审` → conditional + `blocking=true`；`rejected/驳回/expired/过期` → blocked；无记录 → unconfirmed + `blocking=true`。不编造审批时长：材料未给出时长时不写数字。

## 6. 降级与诚实标注

- 未写 → `missing`，绝不默认满足；未命中清单 → `未知`，绝不默认不具备。
- 坏 JSON/坏编码/超限 → `errors`/`degradations`，继续处理其余材料。
- 缺软件（如 PDF 解析不可用）→ `degradations`，不得假装已读。
- 所有 firm 结论必须能由 `selftest.py` 复算；同输入两次运行 `result_sha256` 必须一致。
