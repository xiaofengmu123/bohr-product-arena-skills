# Claim 台账、强度标定与 Results/Discussion 边界

本文档是 scripts 强制执行的规则来源，写作时以 `/input/case.json` 的 output_contract 为准；
本文档只约束“怎么写才站得住”，不决定章节数量。

## 1. Claim 台账 schema（唯一中间表示）

每条正文断言在 `/app/work/claims.json` 中是一条记录：

| 字段 | 含义 | 取值/要求 |
|-|-|-|
| `id` | 稳定标识 | `auto-*` 或人工编号 |
| `kind` | 断言类型 | `number / fact / quote / trend / comparison / causal / mechanism / citation` |
| `strength` | 证据强度 | 见下表七档 |
| `section` | 归属 | `results` 或 `discussion` |
| `locator` | 来源定位 | `文件#段落/页码`、`Fig 2a`、`Table 3` 等 |
| `quote` | 逐字原文 | 材料中的原句或图注/表注原句 |
| `value` / `unit` | 数值与单位 | 图表读数用原始单位，换算另注 |
| `status` | 核实状态 | `candidate / verified / unverified / pending / not_reported` |

规则：**无 locator + 无 quote 的 claim 不得进入 facts_sources 的 verified 态**；缺其一即标 `pending` 或 `unverified`。

## 2. 强度七档与措辞标定（“说得住”）

| strength | 含义 | 推荐措辞（中文） |
|-|-|-|
| `observed` | 直接测量/观察到的现象或数值 | 表明 / 显示 / 测得 / 观察发现 |
| `statistical` | 统计检验结果 | 注明检验方法、P 值或置信区间；“差异有统计学意义” |
| `inferred` | 由数据推断的趋势/关联（弱） | 提示 / 初步表明 |
| `compared` | 组间比较方向与幅度 | 高于 / 低于 / 与…相当 / 变化幅度为…% |
| `interpreted` | 解释性论断 | 提示 / 可能反映 |
| `speculated` | 机制推测 | 推测 / 可能 / 假设，显式标注“推测” |
| `causal` | 因果声称 | 仅在可确立因果时用因果动词；否则“与…相关，尚不能证明因果” |

措辞强度必须 ≤ 证据强度：`observed` 数据不写“证实了机制”；`inferred` 趋势不写“确立”。check 脚本扫描
“首次/国际领先/填补空白/颠覆性/重大突破”等无据措辞并逐条报出，由 Agent 逐条判定是否删除或降级。

## 3. 图表读数规则（“读得准”）

- 读数先声明坐标轴变量与单位，再报数值；误差棒含义（SD / SEM / 95% CI）必须写明来源，不猜。
- 数值写进正文前，与源图表/统计输出逐位对照；保留源的有效数字，不自行增加精度。
- 单位换算（如 mmol/L ↔ mg/dL）必须在 claim 记录换算过程；原文没有的换算因子标 `pending`。
- 多面板图写 `Fig 2a`，补充材料写 `Fig S1`；正文编号与图注、材料编号一致。

## 4. Results 与 Discussion 边界（“分得清”）

- **Results 只陈述观察**：数值、方向、幅度、显著性、组间比较，不解释“为什么”、不引他人文献、不写机制。
- **Discussion 承担解释**：每个论断对应本研究数据或具体文献，说明一致/不一致/可能原因，不堆引用不讲关系。
- 阴性与不一致结果必须在 Results 如实报告，在 Discussion 解释，不得因“不理想”而省略。

## 5. 因果边界与局限（“核得了”）

- 区分相关 / 预测 / 因果：观察性关联、单臂或小样本不得写因果动词；机制推测一律标 `speculated`。
- 局限写到方法、样本、测量、统计四个层面，不写“本研究存在一定局限”这类空话。
- 每条文献对照写入 claim 的 `quote`（原句），并在 result.json `sources` 保留 locator；找不到原文就标 `unverified`，不伪造 DOI/URL。
