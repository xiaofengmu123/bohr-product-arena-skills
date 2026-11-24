# 自检清单与降级规则（Checklist & Degradation）

收口前逐条勾选；未通过按右侧动作处置。每条对应题面评分维度（实际效果 / 使用增益 / 创新增益 / 可靠体验）。

## A. 输出契约（可核验性 → 实际效果·可核验性）

- [ ] `/app/output/result.json` 存在且 `json.load` 可读
- [ ] 含 `case_id`，与 `/input/case.json` 的 id/case_id 一致，缺省用文件名
- [ ] 含 output_contract **全部字段**（原样回填，缺值 `null`，不删未知键）
- [ ] 含 `status / sources / uncertainty / pending_human / change_log`
- [ ] `status` 诚实：缺项用 `partial`，材料不足用 `insufficient_evidence`，输入损坏用 `input_error`
- [ ] `change_log` 从骨架到成稿每次改动都有 `ts/step/note`

## B. 事实一致（写得实 → 实际效果·正确性）

- [ ] 稿件是完整可编辑章节稿，不是提纲/写作建议/素材堆叠
- [ ] 每个数值/单位/样本量/统计显著性/图注都能指回 `material_index.json` 的 locator
- [ ] 正文没有材料外补写；补写处已标 `[待确认]`
- [ ] 摘要与结果、正文与图注、方法与步骤无未说明冲突
- [ ] 运行 `python3 scripts/consistency.py`：无悬空 `[S:]`、无未标注来源的关键数值

## C. 引用对应（引得对 → 实际效果·正确性）

- [ ] 每条 `[C:键]` 在参考文献区有定义，作者/年份/期刊/卷期/DOI 与给定文献一致
- [ ] 每条引用真正支撑所在句，无装饰性、无错配
- [ ] DOI/卷期缺失写 `unknown`，不杜撰；取不到全文只按给定元数据引用
- [ ] 运行 `consistency.py`：无悬空引用、无"已定义未使用"可疑条目

## D. 章节完整（全得起来 → 实际效果·完整性）

- [ ] 覆盖摘要、引言、方法、结果、讨论、结论、参考文献、数据可得性声明等关键部分
- [ ] 图表、表格、声明字段齐备；缺项已标 `[缺失]` 或写入 `uncertainty`
- [ ] 运行 `consistency.py`：无缺失章节、Figure/Table 编号无跳号/重复

## E. 可编辑性（改得动 → 使用增益）

- [ ] 稿件保留 Markdown 章节层级、图注编号、引用格式
- [ ] 可直接在 Word/Markdown 继续修改并逐条复核
- [ ] 要求 docx/xlsx/pptx 时，已另渲染真实 PDF/PNG 预览供检查

## F. 证据溯源（创新增益）

- [ ] `sources` 每条含 `ref_id + locator + 逐字 quote`
- [ ] 冲突信息双来源并列并标 `[冲突待确认]`
- [ ] 缺项在 `pending_human` 与 `uncertainty` 中一致出现

## G. 降级策略（可靠体验 → 异常处理）

规则：**缺什么标什么，绝不编造**；无法产出部分写 `partial` 并在 `uncertainty`/`change_log` 说明 limitations。

| 情况 | 处置 |
|-|-|
| 空输入目录 | 退出码 0，写 `insufficient_evidence`，列出缺口，不编造章节 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，继续处理其余材料，不崩溃 |
| 缺参数/未知参数 | `--case/--task/--output/--work` 缺省指向 `/input/case.json`、`/input/task.md`、`/app/output`、`/app/work` |
| 超大文件 | 按 `--max-bytes`（默认 2MB）跳过并记录，不整体崩溃 |
| PDF/OCR 工具缺失或失败 | 材料标 `[无法解析]`，不凭文件名/标签补内容 |
| 图表缺图注 | 图注标 `[缺失]`，正文相应描述标 `[待确认]`，计入 `uncertainty` |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方 + 来源，标 `[冲突待确认]`；需取舍时显式声明依据 |
| 写盘失败 | 降级到最小合法产物（回退目录或最小 JSON），不崩溃 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录失败原因 |

## H. 收口前最后三查

1. `result.json` 能被 `json.load` 正常读取；
2. `sources` 每条 `ref_id` 都能在 `material_index.json` 或文献条目中找到；
3. `uncertainty` 覆盖全部 `[待确认]`/`[缺失]`/`[冲突待确认]`/`[无法解析]` 标记。
