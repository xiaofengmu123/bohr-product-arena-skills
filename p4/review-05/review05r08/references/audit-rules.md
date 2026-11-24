# 审查判定规则（source-bound audit rules）

本文件是判定层面的规则库，脚本负责"可复核、确定性、契约完整"，Agent 负责"逐条判定"。所有结论都必须回到原文逐字片段（locator + quote），不得依据文件标签猜内容。

## 1. 四类判定（finding type）

| type | 含义 | 成立条件 | 常见场景 |
|---|---|---|---|
| `fact_error` | 事实错误 | 原文陈述与可核对的外部事实矛盾，或材料内部自相矛盾 | 数值矛盾、张冠李戴、错引文献结论 |
| `insufficient_evidence` | 证据不足 | 原文声称了某结论，但缺少支撑该结论的必要证据/方法/数据 | 无统计量、样本量缺失、方法不完整 |
| `norm_violation` | 规范/合规问题 | 违反可指明的条款/政策/伦理要求 | 无知情同意、无 COI、引用格式冲突 |
| `suggestion` | 建议性意见 | 不构成违规，但可改进 | 表述更清晰、补充分层分析 |

判不出类型时宁可写 `insufficient_evidence` 或标 `pending_human`，不可硬套 `fact_error`/`norm_violation`。

## 2. 严重度分级（severity）

- `critical`：影响结论成立、涉及数据真实性问题、无伦理审批却包含可识别个人/受限数据。
- `major`：影响核心论据、引用错误使论断失去支撑、缺失强制声明。
- `minor`：局部瑕疵，不影响主要结论，如格式不统一、个别元数据缺项。
- `info`：仅提示或 `suggestion` 的默认级别。

降级原则：只有位置、依据、影响三者都清楚时才给高严重度；风格差异、学科惯例、个人表述**不计为违规**，最多 `info`。

## 3. 检查方向（category）

基础方向：`academic_integrity`、`citation`、`privacy`、`copyright`、`ethics`、`submission`；
通用质量方向：`quality`、`fact`、`method`、`statistics`、`logic`。
若 `/input/case.json` 提供 `review_facets`/`categories`，以题面为准并覆盖默认值；每条 finding 的 category 必须落在允许集合内。

## 4. 每条问题的四要素（硬要求）

1. **原文位置**：`file` + `locator`（如 `paper.pdf#p3` 或 `manuscript.md#L120`）。
2. **逐字证据**：`quote`，必须能从材料原文逐字找到（validate.py 会独立核对）。
3. **判定依据**：`basis`，说明"依据哪一条款/哪段原文/哪条元数据"，并给出 `basis_kind`。
4. **可执行修改建议**：`revision`，写出作者可直接照做的动作，而不是"建议检查一下"。

`evidence` 字段用于补充说明证据强度（如"方法部分第 3 节未报告样本量"）。

## 5. 依据绑定（basis_kind）

| basis_kind | 含义 | 要求 |
|---|---|---|
| `material_quote` | 依据来自材料内部另一处原文 | basis 引用该处 locator + quote |
| `normative_clause` | 依据来自外部条款/指南 | **必须**同时给 `basis_source`（条款名、URL、版本、日期） |
| `source_metadata` | 依据来自文献元数据（DOI、期刊、作者） | basis 写明核对方式与来源 |
| `pending_human` | 无法自行确认 | 只标位置与类型，不复述敏感内容，写入 `pending_human` |
| `not_reported` | 材料未报告该项 | 记录为缺项，不猜测缺失值 |

禁止编造条款、期刊要求、文献元数据或 DOI：拿不准就降为 `pending_human`/`not_reported`。

## 6. 六个方向的检查细则（每方向都须有"已查/待复核/未查"结论）

1. **学术诚信**：数据/图像复用、重复发表、不当署名、利益冲突声明缺失、致谢与基金不一致。证据 = 两处原文对照或与外部记录对照。
2. **引用规范**：被引文献是否真实、元数据与原文一致、正文论断与引文对应、引而不证、张冠李戴、格式统一性。
3. **隐私与数据合规**：可识别个人信息、受限/未公开数据、是否具授权或伦理审批；提出脱敏/改写建议。
4. **版权与许可**：图/表/代码/数据集的来源标注、许可类型与复用条件，指出需补授权或替换的位置。
5. **伦理审查**：涉及人研究的知情同意、伦理批件编号与适用范围；动物实验规范；风险获益表述与实际设计一致。
6. **投稿要求**：把目标期刊/会议作者指南拆成可核对条目并逐条映射，报缺失/超限/格式冲突，注明指南版本与日期。

通用方向同样逐条核查：`fact`（数值与论断）、`method`（方法可复现）、`statistics`（统计量、样本量、检验方法、多重比较）、`logic`（论证链、因果表述）、`quality`（术语、结构、一致性）。

## 7. 误报抑制

- 风格差异、学科惯例、个人表述 → 不判违规。
- 两个来源同一指标不一致 → 两个都记录，标 `source_conflict`，不擅自选一个。
- 无法确认 → `pending_human` / `not_reported`，写入 `report.pending_review` 与 `pending_human`。
- 脚本产出的 `signals`（引用、伦理、统计等行号）只是**待查候选**，不是结论；每条都必须人工/Agent 判定后再落 finding。
- 每条 finding 的 `quote` 必须逐字可回查；做不到就改成可回查的短语或标记无法回查。
