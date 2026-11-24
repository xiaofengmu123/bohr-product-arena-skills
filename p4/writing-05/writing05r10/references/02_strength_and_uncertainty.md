# 证据强度分级与不确定性表达口径

本文件给出可复用的分级与改写口径，供 Agent 在"说得准"一步使用；与
`core_impl.STRENGTH_RANKS` / `UNCERTAINTY_PHRASES` 保持一致。

## 1. 证据强度等级（由强到弱）

| 等级 | rank | 依据 |
|---|---|---|
| `meta_analysis` | 8 | 同质研究定量合并，异质性已报告 |
| `systematic_review` | 7 | 系统检索 + 纳入/排除标准 + 质量评价 |
| `rct` | 6 | 随机对照，报告分配/盲法/失访 |
| `cohort` | 5 | 前瞻或回顾队列，控制混杂 |
| `case_control` | 4 | 病例对照 |
| `cross_sectional` | 3 | 横断面，只能谈相关/患病，不能谈因果 |
| `case_series` | 2 | 无对照病例系列 |
| `case_report` | 1 | 单例 |
| `expert_opinion` | 0 | 专家意见、共识声明中的观点部分 |
| `preprint` | -1 | 未同行评审的预印本 |
| `unverified` | -2 | 无法核验真实性或来源 |
| `unknown` | -3 | 信息不足，无法分级 |

## 2. 定级规则

- 定级依据**研究设计**，而非作者自称；摘要说"显著改善"但设计是横断面时仍定
  `cross_sectional`。
- 一篇文献支撑多条论断时，各论断按自身所需证据定级，不必相同。
- 一条论断需多篇文献时，取支撑该论断的证据中**最弱但直接相关**的一档，并说明
  "若取最高档会夸大"。
- 样本量、重复验证、共识程度作为强度理由写入 `strength_reason`（IR 中的说明），
  不单独造一个虚构等级。

## 3. 表述—强度相称（说得准）

| 强度 | 正文可用表述 | 应避免 |
|---|---|---|
| meta/systematic/rct | 表明、证实 | 仍不宜把"机制"说成定论 |
| cohort/case_control | 提示、支持、相关 | 证实、导致、因果 |
| cross_sectional | 相关、关联、提示 | 导致、降低、提高、因果 |
| case_series/report | 个案提示、有待验证 | 任何群体结论 |
| expert_opinion | 认为、建议 | 表明、证实 |
| preprint/unverified | 尚未同行评审、初步报告 | 已证实 |

改写动作示例（写入 claim 的 `action`）：
- 相关→因果：把"A 导致 B"改为"A 与 B 相关，因果方向尚待前瞻研究验证"。
- 单篇→共识：把"研究表明 X"改为"一项 n=… 的研究提示 X，尚无重复验证"。
- 强词弱证：把"证实"降为"提示/初步表明"。

## 4. 不确定性限定词库（部分，见 core_impl）

- 强：证实、表明、demonstrated、demonstrates。
- 中：支持、提示、初步表明、suggests、indicates、shows。
- 弱：可能、或许、may、might。
- 展望：有望（表示预期，非既成事实）。

## 5. 冲突与缺失处理

- 冲突证据**不隐藏**：同指标两来源结论相反时，两方都记录，`support=conflict`，
  并写 `pending_human` 说明需人工裁决。
- 缺失支撑：`status=missing_source`，数字/引用/术语无法回溯时不写进 deliverable；
  确需保留则记 `uncertainty` 并标注 `not_reported`。
- 二手转引：`type=secondary`，写清核验路径，不冒充直接引用原始研究。

## 6. 定级与改写示例（示例，非正式案例）

原文摘录（假设）："在横断面调查中，A 组血糖水平低于 B 组（P<0.05）。"

| 项 | 值 |
|---|---|
| claim.statement | A 组血糖低于 B 组（原稿错误写为"A 组治疗可降低血糖"） |
| support | `direct`（原文确实报告组间差异） |
| strength | `cross_sectional`（横断面，只能谈相关/差异） |
| overclaim | 原稿"降低/治疗"为因果措辞，触发 overclaim 预警 |
| action | 改为"A 组血糖水平低于 B 组，差异有统计学意义；因设计为横断面，尚不能推断因果关系" |

该示例只说明定级—表述—改写的对应关系，不冒充任何正式案例结果。
