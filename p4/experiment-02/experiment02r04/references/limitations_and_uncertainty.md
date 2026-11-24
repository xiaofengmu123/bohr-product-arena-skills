# 降级、限制与不确定性（本版重点）

本 Skill 的变体定位是：**限制与不确定性显式化，缺数据不编造**。审查结论的价值
不只在“可行/不可行”，更在于诚实标注“哪里没证据、哪里是估算、缺什么才能下结论”。
以下规则构成不可破坏的底线。

## 1. 证据四态（每个关键数值结论必须落其一）

| kind | 含义 | 置信度 | 落盘要求 |
|---|---|---|---|
| `observed` | 直接引用材料原文数值 | high/medium | `locator` + 逐字 `quote` |
| `derived` | 由脚本算术从材料数值推出 | medium | `formula`（算式）+ 输入数值出处 |
| `assumed` | 显式假设 | low | 写明假设内容与依据，或转 `pending_human` |
| `unreported` | 材料未给 | insufficient | 记为 `missing`，绝不补造数值 |

判定顺序：先问“这数从哪来”。无 `locator`、无 `formula`、无显式假设的数值，
一律视为疑似编造，进入 `unverifiable_claims` 限制登记。

## 2. limitations 六类登记

`result.limitations` 按类登记，每条含 `id / kind / claim / severity / affects /
basis / resolution / needs_expert`。

| kind | 触发条件 | 解除方式 |
|---|---|---|
| `evidence_scope` | 只有部分章节/材料被读取，结论覆盖范围受限 | 补齐未读材料并重判 |
| `arithmetic_uncertainty` | 缺时长/样本/价格输入，或参数为区间/估计 | 补输入或用 `check_range` 写区间结论 |
| `source_quality` | 来源为二手、摘要、未复核或字段冲突 | 回溯原始出处并标 `source_conflict` |
| `missing_contract` | 题目 `output_contract` 字段无法回填或输入不可解析 | 修复输入或原样补 null/[] |
| `conflicting_sources` | 同一指标两来源不一致 | 两条都记录，裁决或转 `pending_human` |
| `unverifiable_claims` | 数值/审批/货期无来源支撑 | 改判 `unreported`/`unconfirmed` |

`severity`：`blocker`（结论不可下）/ `major`（影响某维度判断）/ `minor`（细节缺口）。

## 3. 状态格（status）与诚实标注

| status | 条件 | 必须显式标注 |
|---|---|---|
| `ok` | 七类审查完成、结论可执行、证据可复核 | 无未解除 blocker；残留限制转 `notes` |
| `partial` | 完成部分审查，仍有缺口 | 在 `limitations`/`uncertainty`/`pending_human` 写清缺口 |
| `insufficient_evidence` | 无任何可读输入或材料不足以判定 | 列出全部缺口，不编造方案/数值/结论 |
| `input_error` | `case.json` 无法解析 | 记录解析错误，继续处理其余材料 |

“partial 必须指明限制”是硬规则：`selftest.py` 会校验 status=partial 时
limitations/uncertainty/pending_human 不得全空。

## 4. 降级三层（缺数据 / 缺软件时如实标注）

1. **输入级**：空目录 → `insufficient_evidence`；坏 JSON/坏编码/超限 → 记入
   `errors`/`degradations`，继续处理其余材料。
2. **证据级**：无 locator/quote/公式 → 改判 `missing`/`unconfirmed`，写进限制。
3. **软件级**：PDF 解析等工具不可用 → 记入 `degradations`，标注“该证据未能读取”，
   绝不假装已读。

写盘失败 → 降级到最小合法 JSON（至少 `case_id` + `status` + `errors`），进程退出码 0。

## 5. 不编造负面清单

不得虚构：引用、DOI、数值、价格、审批状态、设备型号能力、样本量、材料货期。
- 未写即 `missing`，不默认满足。
- 未确认即 `unconfirmed` + `needs_expert=true`，不写成可执行事实。
- 无真实材料时输出“材料不足”，不写示例答案。
- 不预写正式答案，不冒充评测分数，不伪造运行结果。

## 6. 可复跑要求

相同输入、相同口径下，两次运行应得到可比较的结论：判断依赖材料原文与
`scripts/review.py` 的确定性算术，而非模型记忆或墙钟时间。所有脚本不写随机数、
不写时间戳（`runtime.started_at` 由平台或 Agent 在收尾时回填，骨架阶段为 null）。
