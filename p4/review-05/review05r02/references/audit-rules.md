# 审查规则与判定工作流（audit-rules）

本文件是审查 Agent 的判定依据库之一。它与 `authority-registry.md` 配套：前者规定"查什么、怎么判定"，后者规定"依据绑到哪里、如何防止编造条款"。

## 1. 十项检查面（facet）

题目要求的六方向 + 四条通用质量面，均需在 `result.json` 的 `report.coverage[]` 中给出结论（`checked_clean` / `finding` / `insufficient_evidence` / `not_applicable`）：

| facet | 检查内容 | 常见信号 |
|---|---|---|
| academic_integrity | 数据/图像复用、重复发表、不当署名、利益冲突声明缺失、致谢与基金不一致 | conflict of interest、基金号、致谢、作者单位 |
| citation | 被引文献真实存在、元数据一致、正文论断与所引文献一一对应、引而不证、张冠李戴、格式不统一 | `[n]`、`(Author, Year)`、参考文献列表 |
| privacy | 可识别个人信息、受限数据、未公开数据，是否具备授权或伦理审批依据，是否脱敏 | 邮箱、电话、身份证号、受试者 ID、患者信息 |
| copyright | 图片/表格/代码/数据集等第三方材料的来源标注、许可类型、复用条件 | 图题、许可、版权、CC BY、GPL、© |
| ethics | 涉及人的研究是否说明知情同意、伦理批件编号与适用范围；动物实验规范；风险获益表述 | informed consent、IRB、批件、动物实验 |
| submission | 目标期刊/会议指南逐条映射，缺失项、超限项、格式冲突，注明指南版本与日期 | 目标期刊、字数/图表限制、作者指南 |
| quality | 表述清晰、术语一致、图表编号、单位、拼写、结构 | 错别字、单位混用、图号错乱 |
| statistics | 检验方法、样本量、效应量、显著性、置信区间、多重比较是否交代 | p 值、n=、标准差、置信区间 |
| method | 方法/参数/数据可得性是否足以复现，试剂仪器参数是否写明 | 数据可用性、代码可用、补充材料 |
| logic | 论证链是否成立，结论是否由证据支持，是否以相关性冒充因果 | "因此""证明""表明"与证据强度 |

## 2. 问题类型（type）

- `fact_error`：可核验的事实错误（数值、文献元数据、方法表述、引用对象与原文不符）。
- `insufficient_evidence`：材料声称某结论但未给出足以支持的证据（样本量、方法、伦理批件、授权等缺失）。
- `norm_violation`：规范/合规问题（隐私未脱敏、版权未授权、伦理审批缺失、投稿格式冲突、学术不端）。
- `suggestion`：不影响结论正确性、但值得改进的建议（可读性、格式统一、补充说明）。

## 3. 严重度分级（severity）

| severity | 适用条件 |
|---|---|
| critical | 直接危及结论成立或学术合规：数据伪造/复用、结论与证据相反、伦理审批缺失且涉及人研究、伪造引用 |
| major | 影响可复现性或合规判断：方法缺失关键参数、统计检验缺失、隐私未脱敏、第三方材料无授权、引用对象错误 |
| minor | 局部缺陷：格式不统一、单一文献格式错误、单位不统一、图号错乱、次要参数缺失 |
| info | 建议性、提示性：表述可改进、术语建议统一、可补充说明 |

依据缺失（`basis.kind == "unavailable"`）时，严重度必须降为 `minor`/`info`，并写入 `pending_human`。

## 4. 误报抑制（最重要）

以下情况不得报为问题：

1. 风格差异、学科惯例、个人表述偏好。
2. 仅凭文件标签或领域标签猜测内容；必须读实际文件。
3. 无法回查原文的"感觉像有问题"。
4. 单次观察到的不一致若存在合理解释（如不同版本、单位换算）应先排除。

候选观察（`work/evidence.json`）只是待判定假设，逐条判定后才可进入 findings。每条 finding 必须带 `verdict`：

- `confirmed`：证据充分，判定成立。
- `downgraded`：成立但严重度低于候选初判。
- `excluded`：核对原文后排除，通常不进入 findings，或仅以 `severity=info` 保留说明已排除。
- `pending`：无法判定，进入 `pending_human` / `not_checked`。

## 5. 判定工作流（逐条）

对每个候选观察执行四步：

1. 读原文：打开 `file` 与 `locator` 指向的片段，核对 `quote` 逐字一致。
2. 找依据：确定该问题对应哪条规范；依据必须绑定到 `authority-registry.md` 规定的来源。
3. 定性与分级：按第 2、3 节确定 type 与 severity；写 `candidate`、`verdict`、`adjudication`。
4. 给修改建议：`revision` 必须可执行（改成什么、补什么、标在哪里），不写"建议完善"这类空话。

冲突信息（同一指标两个来源不一致）两条都记录并标 `source_conflict`；无法确认标 `pending_human` / `not_reported`。

## 6. 覆盖完整性要求

- 十个 facet 必须逐项有结论，不允许只挑最容易的一条出报告。
- 对"已查无问题"的 facet 也要写 `checked_clean` 并附一句说明，证明检查过而非跳过。
- 材料缺失、解析失败、软件缺失必须写入 `errors` / `degradations`，不猜测内容。

## 7. 确定性

- finding 的 `id` 由 `category|file|locator|quote` 哈希生成，同一输入重跑得到相同 id 与排序。
- `main.py merge` 幂等：同一份 findings 重复 merge 不重复、不漂移。
- 时间戳只写入 `change_log` 与 `runtime`，不参与 findings 排序。
