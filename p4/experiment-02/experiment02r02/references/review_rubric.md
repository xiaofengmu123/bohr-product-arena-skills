# 审查口径与判定规则（feasibility rubric）

本文是 `review_core.py` 七类判断的"为什么这么判"依据，供复核与扩展。判定只依赖输入原文可取到的结构、数值与逐字片段；取不到的一律记为缺失或待确认，不默认满足。

## 1. verdict 枚举

| verdict | 含义 | 必须附带 |
|---|---|---|
| `ok` | 有原文依据，且与约束一致 | `evidence`（locator+quote） |
| `conditional` | 可执行，但需先满足可勾选条件 | `conditions` + `evidence` |
| `blocked` | 有硬阻断（越界/货期超窗/成本超预算） | `conditions`（写清缺什么）+ `evidence` |
| `conflict` | 同一指标存在矛盾，或版本出现回归 | `conditions` + `evidence` |
| `missing` | 方案"没写"，无法判断 | 不必带 evidence（证据是"缺失"本身），但写清缺什么 |
| `unconfirmed` | 需人工/外部确认（审批、领域判断、外协渠道） | `conditions` + `needs_expert=true` |

`resource_state`（仅设备/材料维度）：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。状态由合同/清单原文关键词映射，无法映射时为 `未知`。

## 2. 七类约束的判定标准

1. **假设与变量**：假设可证伪且与检验指标对应；自变量/因变量均已声明并给出操作化定义；主判读标准含阈值/方向/统计口径。缺一项即 `missing`，不把"路线清楚"当"可检验"。
2. **对照组**：对照/空白/基线、重复数、随机或盲法至少声明其一。前版有、后版删掉同类要素 → `conflict`（版本回归）。
3. **设备与材料**：方案用到的资源逐项与合同/清单匹配。匹配不到 → `unconfirmed`（不是"不具备"，是"未见合同"）；匹配到但无用量 → `missing`；`需采购/不具备` 且货期 > 方案窗口 → `blocked`。
4. **时间与成本**：全部由算术台账得出。成本 = Σ(单价×用量) + Σ(时租×机时) + 固定费；时长 = Σ步骤时长，按 8h/工作日折算后与排期比；成本与预算比。不可算 → `unconfirmed` 并列出缺什么数。
5. **参数范围**：每个已声明参数与有限参数域比对。数值越界或不在枚举集 → `blocked`；不在 min/step 网格点 → `conflict`；参数无对应域 → `unconfirmed`；参数无取值 → `missing`。
6. **风险与异常**：至少覆盖设备故障、材料批次差异、样本不足、参数越界四类，各配触发条件/处置/责任人。少于四类 → `conditional`；完全没有 → `missing`。
7. **伦理与安全**：关键词命中动物/人体/危化/个人数据后，给对应审批层级、材料清单与预估周期，结论 `unconfirmed + needs_expert=true`。未命中关键词不等于不涉及，同样记为 `unconfirmed`（"方案没写 ≠ 不涉及"）。

## 3. 算术规则（确定性、可复算）

每条台账 `expr` 只使用 `+ - * / **` 与白名单函数（`ceil/floor/round/abs/sqrt/log/log10/exp/min/max`），`terms` 给出每个符号的数值、单位与 locator。`recompute_ok=true` 表示用 `safe_eval(expr, {symbol: value})` 复算等于 `result`。

- **成本**：只累加能匹配到合同/清单的资源项；未匹配项与缺用量项单独列进 `notes`，不塞进总价。固定费按合同去重计一次。
- **时间**：步骤时长按串行累加（未声明并行分组时取保守口径）；`days = total_hours / 8`。等待/周转单独按天累加。
- **样本量**（连续结局，两独立组）：`n/组 = ceil( 2·(z_{1-α/2}+z_{1-β})²·(CV/effect)² / (1-attrition) )`；比例结局用正态近似公式。`z` 值由 `statistics.NormalDist().inv_cdf` 派生并在 terms 标注 `derived=true`。
- **参数域**：`value ∈ [min,max]`；枚举集成员；网格对齐 `(value-min) mod step ≈ 0`。
- **货期/预算**：`lead_days > window_days → blocked`；`cost > budget → blocked`。无参考日期时不臆造"今天"，只与方案自身排期比较。

## 4. 版本比较口径

三个候选版本按七项子分加权评分：

| 子分 | 权重 | 计算 |
|---|---|---|
| hard_blocks | 0.30 | `100 - 25×阻断数`（下限 0） |
| parameter_compliance | 0.20 | `100 × 通过参数数 / 已核查参数数` |
| resource_readiness | 0.15 | 已具备=1.0、可外协=0.7、需采购=0.35、不具备=0、未知=0.3 的均值 |
| schedule_slack | 0.10 | `clamp(50 + 2×slack_days)`，不可算取 50 |
| cost_headroom | 0.10 | `clamp(200×(1-cost/budget))`，不可算取 50 |
| sample_adequacy | 0.10 | `100×min(1, n_total/(n_required×组数))`，不可算取 50 |
| control_strength | 0.05 | ok=100、conditional=50、missing/其他=0 |

**排序是审查辅助口径，不是测量真值**：权重在 `references/review_rubric.md` 内可复核；`plan_comparison.sensitivity` 给出 hard_blocks 权重 0.15↔0.45 时排序是否翻转，翻转过则标注 `sensitive_to_weight`。推荐版本只作为起点，必须叠加阻断项与条件结论使用。

## 5. 降级与诚实标注

| 场景 | 处置 |
|---|---|
| 空输入目录 / 无 case.json | `status=insufficient_evidence`，列缺口，退出码 0 |
| case.json 坏 JSON / 坏编码 | `status=input_error`，记 `errors`，保留契约字段，不推断方案 |
| 单文件 > 2MB | 跳过并记 `degradations` |
| PDF / 图片 / OCR 材料 | 记 `needs_agent_extraction`，脚本不臆测内容；须由 Agent 用平台工具取文本后再补证据 |
| 引文无法在原文逐字命中 | 该 evidence 记 `quote_verified=false`，不删除；作为线索而非复核依据 |
| 软件缺失 / 解析失败 | 记 `degradations`，标注"该证据未能读取" |
| 伦理/安全/设备可用性未经审批 | `unconfirmed + needs_expert=true`，绝不写成已满足 |
| 写盘失败 | 降级为最小合法 JSON，退出码 0 |

## 6. 证据定位与引文

- 定位格式：`case.json#plans[0].steps[2].duration_h`、`case.json#contracts.equipment[0]`、`task.md#L94`。
- `quote` 是"键: 值"级逐字片段（不带外层 `{}`），按去空白包含关系回原文校验；`quote_verified=true` 才可作为复核依据。
- 冲突不隐藏：同一指标两处不一致时两处都记录，并标 `conflict` 或在 notes 写 `source_conflict`。
- 不虚构 DOI、价格、审批状态、日期；缺了写 `missing/unconfirmed/pending_human`。
