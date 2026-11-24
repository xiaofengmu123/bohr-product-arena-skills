# 结构问题分类与判断规则

本文件是六类结构问题、证据定位格式、论点—论据映射规则与结论边界动词表的唯一规则来源。模型必须遵守，脚本候选仅作锚点。

## 1. 六类维度与可执行动作

每条诊断项统一为 `{id, dimension, severity(high|medium|low), problem, locator, quote, edit_action}`。`edit_action` 必须是可执行编辑动作，禁止只写"建议优化"。

| 维度 | 定义 | 检查问题 | 典型缺陷 | 可执行动作模板 |
|-|-|-|-|-|
| research_question | 研究问题与贡献主张对齐 | 研究问题是否被清楚提出、是否贯穿全文；贡献主张是否与实际完成工作一致 | 问题与方法/结果各说各话；贡献大于实际完成的工作 | "将第 X 段研究问题改为…；删除结论中未受数据支撑的贡献主张 Y" |
| chapter_structure | 章节结构与叙事顺序 | 章节顺序、篇幅分配、信息释放顺序是否服务论证 | 该前置的铺垫后置；该后移的细节前置；应合并的章节分开 | "将 §X 中 Y 段前移到 §Z 之后；§A 与 §B 合并为…；§C 拆分为…" |
| argument_chain | 论证链与论据对应 | 逐条抽取论断与支撑论据，找断裂、跳跃、循环论证、论据强度不足 | 论断无对应论据；用相关论证因果；循环引用自身结论 | "在 §X 论断 A 处补上来自 Table N / Fig M 的论据；将 '证明' 收窄为 '提示'" |
| paragraph_relations | 段落关系 | 段落间过渡与逻辑衔接 | 段落间缺少过渡或逻辑跳跃 | "在 §X 第 2 段首补过渡句，承接上一段结论并引出本段论点" |
| repetition | 重复内容 | 同义重复、跨章节重复表述 | 方法被重复叙述；结论与摘要逐字重复 | "删除 §X 第 3 段，与 §Y 第 1 段同义（相似度 0.82），保留 §Y" |
| conclusion_boundary | 结论边界与表述尺度 | 结论是否超出数据与实验条件范围、是否遗漏限制、相关是否写成因果 | 无统计差异却称"显著"；相关性写成因果；遗漏样本/条件限制 | "将 §X 'X 导致 Y' 收窄为 'X 与 Y 相关（r=0.42, p<0.05），因果关系需进一步实验'" |

## 2. 定位与逐字引文（证据硬要求）

- `locator` 格式：`文件相对路径#L<起始行>` 或 `文件相对路径#图表编号`。示例：`paper.md#L42`、`paper.pdf#L17`、`paper.md#Table2`。
- 脚本产出的 `candidate_*` 已带 locator；模型引用时须保留该 locator，不得自行重编号。
- `quote` 必须是原文逐字片段（含标点、数字、单位），不得改写、概括或翻译。若原文为图片/公式，quote 写"[图片/公式，见 locator]"并进 pending_human。
- 若某条判断找不到原文依据，属于"虚构缺陷"，必须删除或改写判断，不得保留。

## 3. 论点—论据映射规则

1. 抽取论断：包含断言动词（表明/显示/发现/suggest/indicate/show 等）或含统计量（p 值、百分比、效应量）的句子。
2. 为每条论断匹配论据：数据表、图、统计输出、文献引用；匹配不到则标 `missing_evidence`。
3. 强度分级：`strong`（有图/表/统计直接支持）、`medium`（有间接或部分支持）、`weak`（仅论断无直接论据）、`unsupported`（无任何支持）。
4. 循环论证：论断 A 的论据是论断 A 本身或由 A 推出的 B，标 `circular`。

## 4. 结论边界动词表

- 因果/强断言（需实验因果设计才可）：导致、引起、造成、促进、抑制、驱动、决定、证明、证实、cause(s)、lead(s) to、induce(s)、drive(s)、determine(s)、demonstrate(s)。
- 相关/弱断言（观测数据可用）：相关、关联、伴随、呈相关、associated、correlated、linked、relationship、trend。
- 越界候选规则：句中同时出现"相关/associated"与因果动词 → 标 `correlation_as_causation`；观测/横断面研究使用因果动词 → 标 `causal_overreach`；无统计差异却写"显著/差异" → 标 `significance_overreach`。
- 脚本只给候选标记（见 `candidate_boundary`），最终判定由模型结合研究设计与统计输出给出。

## 5. 章节功能拆解对照

| 章节 | 应有功能 | 常见缺位 |
|-|-|-|
| Introduction | 提出研究问题、缺口与贡献主张 | 问题不显式、贡献主张缺失 |
| Methods | 可复现的样本、条件、测量与统计方法 | 缺样本量/统计方法，结果无法复现 |
| Results | 只陈述观察与统计结果 | 混入解释、越界论断 |
| Discussion | 解释、文献对照、机制（标注推测）、局限 | 无局限、把推测写实 |
| Conclusion | 与问题闭环、不超过数据范围 | 重复摘要、新贡献未支撑 |

判定时以"该章节应有功能是否缺失/越位"为依据，不把风格偏好（用词、句式）当结构错误。

## 6. 严重度分级

- `high`：导致论证链断裂、结论不可信或无法复核的问题。
- `medium`：影响叙事效率、可读性或覆盖完整性。
- `low`：局部衔接、轻微重复或表述尺度微调。

## 7. 示意诊断项（示例，非正式案例结果）

以下为格式示范，locator 与 quote 必须替换为真实稿件内容；不得在正式案例中套用。

```json
[
  {"id": "D1", "dimension": "conclusion_boundary", "severity": "high",
   "problem": "观测研究把相关表述为因果", "locator": "paper.md#L15",
   "quote": "X 导致 Y", "edit_action": "将 '导致' 收窄为 '与…相关（r=0.42, p<0.05）'"},
  {"id": "D2", "dimension": "repetition", "severity": "medium",
   "problem": "方法步骤与 §2 逐字重复", "locator": "paper.md#L41",
   "quote": "采用 120 例受试者，随机分为两组",
   "edit_action": "删除本段，改为 '方法详见 §2.1'"},
  {"id": "D3", "dimension": "argument_chain", "severity": "high",
   "problem": "论断无对应论据", "locator": "paper.md#L58",
   "quote": "本方法显著优于基线",
   "edit_action": "补 Table 2 的 p 值与效应量，或将 '显著优于' 收窄为 '在任务 A 上优于'"}
]
```

## 8. 论点—论据映射示意（示例）

- 论断 C1（"X 改善 Y"）→ 论据：Table 1（12.4% vs 8.1%, p=0.031）→ 强度 `strong`。
- 论断 C2（"X 导致 Y"）→ 论据：仅相关性统计，无因果设计 → 强度 `unsupported`，标 `circular` 或 `causal_overreach`。
- 论断 C3（"方法显著优于基线"）→ 论据缺失 → `missing_evidence`，进 pending_human。

强度只由真实论据决定；无法匹配时写 `unsupported`/`missing_evidence`，不虚构支持。

