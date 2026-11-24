# 六维结构诊断规则（规则与检查清单）

本文定义论文结构诊断的六维分类、症状、判定规则、可执行编辑动作与证据要求。所有诊断结论必须满足三点：定位到原文、给出逐字依据、给出可执行动作；禁止虚构缺陷或把风格偏好当结构错误。

## 0. 发现记录格式

每个发现（finding）字段：

- `id`：稳定编号（F01、F02……）
- `dimension`：六维之一（见下）
- `locator`：章节/段落/句子定位，如 `§2.3 / para[4] / sent[2]`
- `type`：问题类型（各维度给出清单）
- `severity`：`critical`（破坏论证闭环或结论越界）/ `major`（影响可读或论据链）/ `minor`（可改进，非错误）
- `quote`：原文逐字片段，覆盖被判断的关键子句，不得改写
- `edit`：可执行编辑动作，动词用 move / merge / split / narrow / insert-transition / insert-evidence / reword-scale / delete / reorder，并写明目标位置与具体做法
- `basis`：判定依据（规则编号 + 上下文说明）
- `decision`：作者复核建议 `accept` / `partial` / `reject` 及理由

脚本产出的 `claims` / `redundancy` 只是候选锚点；模型必须逐条核对原文后才可作为发现，不能直接当作缺陷。

## 1. 六维与判定规则

### 1.1 研究问题（research_question）
检查研究问题是否被明确提出、是否贯穿全文、贡献主张与实际完成工作是否一致。
- 症状类型：`problem_missing`、`contribution_misaligned`、`contribution_overclaim`、`question_method_diverged`。
- 规则 RQ-1：引言末尾或方法前应有明确的研究问题/假设/目标，可定位到原句；找不到则标 `problem_missing`。
- 规则 RQ-2：结论声称的贡献必须在方法与结果中有对应证据；宣称"首次/首个/唯一"需有原文范围限定，否则标 `contribution_overclaim`。
- 编辑动作：`insert`（补问题句及位置）、`reword-scale`（收窄贡献表述）、`narrow`（把绝对贡献改为有条件贡献）。

### 1.2 章节结构（chapter_structure）
检查章节顺序、篇幅分配、信息释放顺序是否服务论证。
- 症状类型：`background_missing`、`detail_too_early`、`sections_mergeable`、`section_splittable`、`order_inverted`、`length_imbalanced`。
- 规则 CS-1：读者需要的前置概念/方法背景应出现在被使用之前；先使用后定义，标 `detail_too_early` 或 `background_missing`。
- 规则 CS-2：承担单一功能的章节被拆散且被无关内容打断，建议合并；一个章节混载多个功能，建议拆分。
- 编辑动作：`reorder`（把第 X 节移到 Y 前）、`merge`（与哪段合并）、`split`（从哪拆出新段）。

### 1.3 论证链（argument_chain）
逐条抽取论断与支撑论据，找断裂、跳跃、循环论证、论据强度不足。
- 症状类型：`claim_without_evidence`、`evidence_without_claim`、`circular_reasoning`、`gap`、`leap`。
- 规则 AC-1：每条论断须在邻近上下文或跨章节有对应论据（数据/文献/推导）；无对应标 `claim_without_evidence`。
- 规则 AC-2：论据强度须匹配论断强度；用单一样本或相关性结果支撑因果/普遍结论，标 `evidence_insufficient`。
- 规则 AC-3：用 A 证 B、又用 B 证 A，标 `circular_reasoning`。
- 编辑动作：`insert-evidence`（补证据位置）、`narrow`（收窄论断）、`reword-scale`（因果改为相关）。

### 1.4 段落关系（paragraph_relation）
检查段落间过渡、逻辑衔接、主题统一。
- 症状类型：`transition_missing`、`logic_jump`、`topic_shift`、`paragraph_overlong`、`paragraph_too_short`。
- 规则 PR-1：相邻段落主题突变且无过渡句，标 `transition_missing`。
- 规则 PR-2：一段多主题混排建议拆分；多段同主题建议合并。
- 编辑动作：`insert-transition`（补过渡句位置）、`split`、`merge`。

### 1.5 重复内容（redundancy）
识别同义重复、跨章节重复表述。
- 症状类型：`near_duplicate`、`cross_section_duplicate`、`restated_unnecessarily`。
- 规则 RD-1：两处文字高度重叠（脚本候选需人工/模型核对），标 `near_duplicate`；保留论证需要的一处，删除另一处或改为交叉引用。
- 规则 RD-2：结论与摘要/引言重复表述结论，标 `cross_section_duplicate`；一处展开、一处概括。
- 编辑动作：`delete`（删除重复处）、`merge`（合并保留一处）、`reword`（改为指向前文）。

### 1.6 结论边界（conclusion_boundary）
检查结论是否超出数据与实验条件范围、是否遗漏限制、是否把相关性写成因果。
- 症状类型：`overclaim`、`causal_from_correlational`、`missing_limitation`、`generalization`。
- 规则 CB-1：结论措辞超过样本/方法/条件范围（如小样本→普适结论），标 `overclaim`；编辑动作 `narrow`。
- 规则 CB-2：只有相关性证据却用因果动词（导致/引起/使），标 `causal_from_correlational`；编辑动作 `reword-scale`（改为相关/关联/提示）。
- 规则 CB-3：正文未交代局限或仅一句空话，标 `missing_limitation`；编辑动作 `insert`（补局限位置）。

## 2. 证据与可复核性

- 每个发现必须含 `quote`（原文逐字）与 `locator`，能独立指回原文位置。
- 冲突信息不隐藏：同一指标两个来源不一致时，两处都记录并标 `source_conflict` 写入 uncertainty。
- 不确定或缺材料时标 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。
- 稿件缺章节/缺参考文献/图表公式缺失/被截断，须在 limitations 中如实记录，不推断缺失内容。

## 3. 与评分维度的对应

- 实际效果：六维覆盖、定位准确、可执行动作、逐字依据。
- 创新：论点—论据映射（1.3）、跨章节冗余检测（1.5）、结论边界比对（1.6）。
- 可靠：缺章节/缺参考文献/缺图/截断的识别与处置（第 2 节）。
