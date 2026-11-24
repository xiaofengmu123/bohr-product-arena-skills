# 自检清单与降级规则（Checklist & Degradation）

按评分维度组织的收口前逐条核查。每条未通过时按右侧动作处置。

## A. 输出契约（实际效果·结果可核验性）

- [ ] `/app/output/result.json` 存在且为合法 JSON
- [ ] 含 `case_id`，与 `/input/case.json` 一致
- [ ] 含 output_contract 的**全部**字段（见 conventions.md）
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log`
- [ ] `status` 诚实（缺项用 `partial`，不虚标 `complete`）
- [ ] `change_log` 从骨架到成稿每步留痕
- [ ] 运行 `python3 scripts/selftest.py --case /input/case.json --out /app/output/result.json` 退出码 0

## B. 事实一致（写得实）

- [ ] 稿件是完整可编辑章节稿，不是提纲/写作建议/素材堆叠
- [ ] 每个数值/单位/样本量/统计显著性/图注都能指回 `material_index.json` 的 locator + quote
- [ ] 正文没有凭领域常识补写的、材料中不存在的事实；补写处已标 `[待确认]`
- [ ] 摘要与结果、正文与图注、方法与步骤之间无未说明冲突
- [ ] 运行 `evidence_map.py`：无悬空来源、无未标注来源的关键数值

## C. 引用对应（引得对）

- [ ] 每条 `[C:键]` 在参考文献区有定义，作者/年份/期刊/卷期/DOI 与给定文献一致
- [ ] 每条引用真正支撑所在句，无装饰性、无错配引用
- [ ] DOI/卷期缺失写 `unknown`，不杜撰；取不到全文时只按给定元数据引用
- [ ] 运行 `evidence_map.py`：无悬空引用、无「定义未用」的可疑条目

## D. 章节完整（全得起来）

- [ ] 覆盖题目/学科模板要求的摘要、引言、方法、结果、讨论、结论、参考文献、数据可得性声明等
- [ ] 图表、表格、声明字段齐备；缺项已显式标 `[缺失]` 或写入 uncertainty
- [ ] Figure/Table 编号一一对应，无跳号/重号

## E. 可编辑性（改得动）

- [ ] 稿件保留 Markdown 章节层级、图注编号、引用格式
- [ ] 可在 Word/Markdown 中继续修改并逐条复核
- [ ] 要求 docx/xlsx/pptx 时已另渲染真实 PDF/PNG 预览

## F. 降级规则：缺什么标什么，绝不编造

| 情况 | 处置 |
|-|-|
| PDF 解析缺失/失败 | 用可用工具；失败标 `[无法解析]`，不凭文件名/标签补内容 |
| 图表缺图注 | 图注标 `[缺失]`，正文相应描述标 `[待确认]`，计入 uncertainty |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段写 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方 + 来源，标 `[冲突待确认]`；取舍时显式声明依据 |
| 材料超长 | 按可用部分产出 `partial`，说明未处理部分及原因 |
| 引用格式不认识 | 统一为 conventions.md 的最小可核格式 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录失败原因 |

## G. 收口前最后三查

1. `json.load` 能正常读取 result.json；
2. `sources` 每条 ref_id/locator 能在 material_index 或文献条目中找到；
3. `uncertainty`/`pending_human` 覆盖全部 `[待确认]`/`[缺失]`/`[冲突待确认]`/`[无法解析]` 标记。

## H. 早落盘硬规则（本版重点）

1. 运行开始 120 秒内必须执行 `init_result.py` 落盘 partial 骨架；
2. 之后每步只改 result.json 对应字段并追加 `change_log`，不整体重写；
3. 时间/请求到上限时，以当前 `partial` 状态收口并如实记录 limits，绝不虚标 complete。
