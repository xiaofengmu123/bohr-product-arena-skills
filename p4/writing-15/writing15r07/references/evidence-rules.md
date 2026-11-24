# 证据溯源与 Quote 规范化规则

## 原则

每个关键论断、数字、引用、改动都要能指回材料中的**逐字片段**（locator + quote）。没有真实材料时不示例填充；缺就写 `pending_human` / `not_reported`，不虚构引用、DOI、页码、数值。

## locator 格式

- 文件段落：`materials/paper.md#p3`（第 3 段）。
- 页码：`materials/source.pdf#page=5`。
- 行号：`materials/notes.txt#L12-L15`。
- 结构化字段：`case.json#output_contract`。
- 材料外、无法定位：写 `not_reported`，并在 `uncertainty` 说明。

## quote 要求

- quote 必须是材料中的原文字符串（逐字），不是转述；转述放进 `claim`。
- 从 PDF 提取的文本可能有换行/空格误差，quote 尽量取短而精确的片段。
- 一条 evidence 至少含 `claim + locator + quote`；`source` 指向材料文件。

## 冲突与不确定

- 同一指标两个来源不一致：两条都记录，`kind=source_conflict`，在 `uncertainty` 写明分歧。
- 引用存在但找不到原文 / 元数据缺失：`confidence=low`，标 `pending_human`。
- 材料编码坏、页面截断、OCR 噪声：在 `degradations` 记录，不影响其余材料处理。

## 修改记录（change_log）

- 每次实质改动记一条：`action`、`target`（章节/段落定位）、`before`（原句）、`after`（改后句）、`reason`、`status`。
- 保留作者原句，使修改可回退、可比较、可说明；机器改写句与原文都要保留。
- 未落实或需人工确认的改动 `status=pending`，进入 `pending_human`。

## 禁止事项

- 不新增材料外事实；不编造文献、DOI、页码、实验数据、基准分数。
- 不把"推测"写成"结论"：推断写 `uncertainty`，确立的事实才用确定措辞。
- 不隐藏冲突或失败：冲突、缺口、运行异常都要如实写进对应字段。
