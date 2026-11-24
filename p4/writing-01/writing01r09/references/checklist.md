# 自检清单与降级规则（Checklist & Degradation）

收口前的逐条核查清单，按评分维度组织。标记 `[自动]` 的项由 `python3 scripts/main.py verify` 检测。

## A. 输出契约（可核验性）

- [ ] `[自动]` `/app/output/result.json` 存在且为合法 JSON
- [ ] `[自动]` 含 `case_id`，与 `/input/case.json` 一致
- [ ] `[自动]` 含 output_contract 的**全部**键且已填充（非 `null`）
- [ ] `[自动]` 含 `status`/`sources`/`uncertainty`/`pending_human`/`change_log`
- [ ] `status` 与实际完成度一致：缺项用 `partial`，缺材料用 `insufficient_evidence`，不得虚标 `complete`
- [ ] `change_log` 记录从骨架到成稿的每次改动

## B. 事实一致（写得实）

- [ ] 稿件是完整可编辑章节稿，不是提纲/写作建议/素材堆叠
- [ ] 每个数值/单位/样本量/统计显著性/图注都能指回 `material_index.json` 的具体位置（locator + quote）
- [ ] 正文没有凭领域常识补写的、材料中不存在的事实；补写处已标 `[待确认]`
- [ ] 摘要与结果、正文与图注、方法与步骤之间无未说明的冲突
- [ ] `[自动]` 无悬空 `[S:材料ID]`（每个都在 sources 中有 `ref_id`）

## C. 引用对应（引得对）

- [ ] 每条 `[C:键]` 在参考文献区有定义，作者/年份/期刊/卷期/DOI 与给定文献一致
- [ ] 每条引用真正支撑它所在句子的论断，无装饰性、无错配引用
- [ ] DOI/卷期缺失时写 `unknown`，不杜撰；文献取不到全文时只依据给定元数据引用
- [ ] `[自动]` 无悬空 `[C:键]`（每个都有 `[C:键]:` 定义行）

## D. 章节完整（全得起来）

- [ ] 覆盖摘要、引言、方法、结果、讨论、结论、参考文献、数据可得性声明等关键部分
- [ ] 图表、表格、声明字段齐备；缺项已显式标注 `[缺失]` 或写入 `uncertainty`
- [ ] Figure/Table 编号一一对应，无跳号、无重复

## E. 可编辑性（改得动）

- [ ] 稿件保留 Markdown 章节层级、图注编号、引用格式
- [ ] 可直接在 Word/Markdown 中继续修改并逐条复核
- [ ] 要求 docx/xlsx/pptx 时，已另渲染真实 PDF/PNG 预览供检查

## F. 降级策略（缺数据/缺软件时）

规则：**缺什么标什么，绝不编造。** 无法产出的部分写成 `partial`，写入 `uncertainty`/`change_log` 的 limitations。

| 情况 | 处置 |
|-|-|
| PDF 解析工具缺失/失败 | 用可用工具提取；失败则标 `[无法解析]`，不凭文件名/标签补写内容 |
| 图表缺图注 | 图注标 `[缺失]`，正文相应描述标 `[待确认]`，计入 `uncertainty` |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段写 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方+来源，标 `[冲突待确认]`；取舍时显式声明依据 |
| 材料超长 | 按可用部分产出 `partial`，说明未处理部分及原因 |
| 引用格式不认识 | 统一为 `[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 最小可核格式 |
| 空输入目录 | `insufficient_evidence`，退出码 0 |
| 坏 JSON/坏编码 | 捕获写入 `errors`/`degradations`，继续处理其余材料 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录失败原因 |

## G. 收口前最后三查

1. `python3 -c "import json; json.load(open('/app/output/result.json', encoding='utf-8'))"` 能正常读取；
2. `sources` 每条 `ref_id` 都能在 `material_index.json` 或文献条目中找到；
3. `uncertainty`/`pending_human` 覆盖稿件中全部 `[待确认]`/`[缺失]`/`[冲突待确认]`/`[无法解析]` 标记。
