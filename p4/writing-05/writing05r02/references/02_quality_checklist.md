# 自检清单与失败处置（checklist & degradation）

交付前与每个案例收尾时逐条勾选；对应评分维度（实际效果 / 使用增益 / 创新增益 / 可靠体验 / 生态贡献）。

## 1. 实际效果（40 分）

- [ ] 论断清单、证据表、成稿三段交付齐备；缺段写明原因。
- [ ] 每条实质论断（数字/术语/引用/图表/关键论断）都能指回 `source_doc + locator + 逐字 quote`。
- [ ] 每条引用真实存在、元数据与原文表述一致；二手转引已标注。
- [ ] 每条论断有 `strength`、`uncertainty`、`support` 三要素。
- [ ] 原文只支持"弱版本"时，正文未写成"强版本"（相关≠因果、单篇≠共识、小样本≠普适）。

## 2. 使用增益（25 分）

- [ ] 同稿同要求下比"通用建议"更具体：每条给出 `quote` + 精确 `locator`，作者无需回原文盲找。
- [ ] 证据强度与不确定性一次给全，减少"补引用/改写过度推断"的返工轮次。
- [ ] 同稿同版本同口径记录在 `sources` / `change_log`，可支撑公平对照。

## 3. 创新增益（15 分）

- [ ] 含至少一项结构级产物：论断切分（claim split）、论断—来源多对多映射、证据强度分级、不确定性控制。
- [ ] `core_impl.py` 的切分/校验/locator 模板 + `references/01` 口径可独立复用。

## 4. 可靠体验（10 分）

- [ ] 缺章节/无参考文献/图表公式缺失/格式混乱/截断均识别并写入 `limitations`。
- [ ] 重复运行不覆盖已改进结果（`scaffold` 跳过非 `in_progress` 版本）。
- [ ] 空目录、坏 JSON、坏编码、缺 pdftotext 均降级不崩溃（`selftest` 覆盖）。

## 5. 生态贡献（10 分）

- [ ] SKILL.md / scripts / references 齐备；依赖（仅标准库）、路径、限制写清。
- [ ] 最小示例标注为"示例"，不冒充正式案例结果。

## 失败处置表

| 场景 | 处置 | status |
|---|---|---|
| 无材料文本 | 只写已验证部分，`limitations` 写明缺口，不补造正文 | `partial` / `insufficient_evidence` |
| 缺 pdftotext | 用标准库兜底读文本类材料；仍失败记 `limitations` | `partial` / `failed` |
| 仅摘要、无全文 | 标注 `UNK` 强度、`not_reported`，可核验部分单独列出 | `partial` |
| 原文与摘要矛盾 | 两条都记，标 `conflict`，待人工确认 | `partial` |
| 证据相互冲突 | 不隐藏，同时呈现并标 `conflict` | `partial` |
| 格式不支持/权限不足 | 记 `degradations`，跳过该材料继续处理其余 | `partial` |
| 图表/公式不可读 | 不猜内容，记 `limitations`："该处有图/公式但无法读取" | `partial` |
| 数字/引用无法回溯 | 不写入结论性正文；确需保留则标 `uncertainty` | `partial` |
| 坏 JSON / 坏编码 | 捕获并记 `errors`/`degradations`，继续处理其余材料 | 不崩溃 |

**硬约束**：不编造评测分数、运行结果、基准对比或隐藏测试；结果只来自本次实际运行。所有状态必须诚实：材料不足就 `insufficient_evidence`，不得写 `complete`。
