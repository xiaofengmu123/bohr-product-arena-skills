# locator 规范与证据溯源规则

本文件定义"说得清"的技术规范：每条判断、数值、论断都要能指回原文位置与逐字依据，作者复核后能独立判断采纳、部分采纳或驳回。规则可复用于其他写作类 Skill。

## 1. locator 格式

- 正文句级：`doc://<相对文件名>#sec<章>.para<段>.sent<句>`，例如 `doc://manuscript.md#sec3.para2.sent1` 表示文件 manuscript.md 第 3 章第 2 段第 1 句。
- 章/段/句序号均从 1 起，稳定、可复现；序号由 `scripts/parse.py` 按文件出现顺序生成。
- 图表定位：`doc://<相对文件名>#fig<序号>`、`doc://<相对文件名>#tab<序号>`。
- 无章节标题的稿件：整体视为一个 `sec1`（heading 记 `(no headings)`，role=`body`），段落仍可定位。
- PDF 未经文本抽取时先渲染/OCR 得到文本，再用"文件+页号"补充定位：`<文件>#page<页号>`。

## 2. 逐字 quote 要求

- 每个写入 `sources` 的条目必须有 `quote`（原文逐字引用，≤240 字符，截断处不加省略号改写，可在末尾加"…"标识截断）。
- 逐字引用不得改写原文用词；翻译/摘要必须另存 `claim` 字段并保留原文 `quote`。
- 图表类结论：quote 记图注或表头的原文片段，另在 `source_ref` 注明图表编号与读数。

## 3. 冲突与缺口处理

- 同一指标两个来源不一致：两个都记录，`source_ref` 各自标明，并在 `uncertainty` 标 `source_conflict`，不擅自取舍。
- 材料缺失、数值不明、单位/显著性无法确定：不猜测，写 `uncertainty`（原因 + 影响范围），正文用 `[待确认：xxx]` 或稳健表述。
- 引用找不到或文献不全：只写已有文献支持的论断，缺失处标"无文献支持"。
- 稿件缺章节、无参考文献、图/公式缺失、稿件被截断：作为结构问题或材料缺口写入 `version_boundary.gaps`，不臆造缺失内容。

## 4. 可回溯性自检口径

交付前逐条确认：

1. 每条 diagnosis / edit_plan / argument_map 条目都有 locator；需要 quote 的条目都有逐字 quote。
2. `sources` 中每个 claim 可被原文片段独立核对。
3. 冲突与缺口都出现在 `uncertainty` / `pending_human`，未隐藏。
4. 脚本候选信号与最终诊断可对应：误报已剔除、漏报已补记。
5. 所有可编辑产物路径都在 `artifacts` 中登记，且文件真实存在于 `/app/output/`。
