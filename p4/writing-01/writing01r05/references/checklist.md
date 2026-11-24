# 自检清单与降级规则（Checklist & Degradation）

收口前逐条核查；未通过时按右侧动作处置。对应关系：A/B/C/D/E/F 与
`scripts/selftest.py` 的维度一致，脚本给出可复核的逐条结果。

## A. 输出契约完整（可核验性）

- [ ] `/app/output/result.json` 存在且为合法 JSON（`json.load` 可读）
- [ ] `case_id` 与 `/input/case.json` 的 id/case_id 一致
- [ ] output_contract 的**全部键**在 result.json 中出现，缺值给 `null`/`[]`，未改名未删键
- [ ] 含 `status`/`sources`/`uncertainty`/`pending_human`/`change_log`/`errors`/`degradations`
- [ ] `status` 与实际完成度一致（缺项用 `partial`，不得虚标 `complete`）
- [ ] `change_log` 从骨架到成稿逐次留痕（ts/step/note）

## B. 事实一致（写得实）

- [ ] 稿件是完整可编辑章节稿，不是提纲/写作建议/素材堆叠
- [ ] 每个数值/单位/样本量/显著性/图注都能指回 `sources` 或 `material_index.json` 的具体位置
- [ ] 无凭领域常识补写的、材料中不存在的事实；补写处标 `[待确认]`
- [ ] 摘要与结果、正文与图注、方法与步骤之间无未说明冲突
- [ ] `evidence.py`：无悬空 `[S:key]`、无未标注来源的事实性数值

## C. 引用对应（引得对）

- [ ] 每条 `[C:键]` 在参考文献区有 `[C:键]: ...` 定义
- [ ] 作者/年份/期刊/卷期/DOI 与给定文献一致；DOI/卷期缺失写 `unknown`
- [ ] 每条引用真正支撑所在句，无装饰性、无错配引用
- [ ] `evidence.py`：无悬空 `[C:键]`、无"定义未使用"的可疑条目

## D. 章节完整（全得起来）

- [ ] 覆盖题目/学科模板要求的摘要、引言、方法、结果、讨论、结论、参考文献、数据可得性声明等
- [ ] 图表、表格、声明字段齐备；缺项显式标 `[缺失]` 并写入 `uncertainty`
- [ ] Figure/Table 编号一一对应，无跳号重号

## E. 可编辑性（改得动）

- [ ] 稿件保留 Markdown 层级、图注编号、引用格式
- [ ] 可在 Word/Markdown 中直接继续修改并逐条复核
- [ ] 要求 docx/xlsx/pptx 时，已另渲染 PDF/PNG 预览

## F. 降级策略（缺数据/缺软件时如实标注）

核心原则：**缺什么标什么，绝不编造**；写 `partial` 结果并在 `uncertainty`/`degradations`/`change_log` 说明。

| 情况 | 处置 |
|-|-|
| PDF 解析失败 | 标 `[无法解析]`；脚本已试 `pdftotext`，失败即记录，不凭文件名补内容 |
| 图表缺图注 | 图注标 `[缺失]`，正文描述标 `[待确认]`，计入 `uncertainty` |
| 文献取不到全文 | 只用给定元数据；缺失字段写 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方 + 来源，标 `[冲突待确认]`；取舍须显式声明依据 |
| 材料超长/坏编码 | 记录到 `degradations`，按可用部分产出 `partial`，说明未处理部分 |
| 引用格式不认识 | 统一为 `[C:键]: 作者. 题目. 期刊 年份;卷(期):页. DOI` 最小可核格式 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，失败原因写入 `errors` |

## G. 收口前最后三查

1. `python3 scripts/selftest.py` 全部 A–F 项为 PASS（或有据 WARN）；
2. result.json 的每个 `sources[].ref_id` 都能在材料索引或参考文献条目中找到；
3. `uncertainty`/`pending_human` 覆盖稿件内全部 `[待确认]`/`[缺失]`/`[冲突待确认]`/`[无法解析]` 标记。
