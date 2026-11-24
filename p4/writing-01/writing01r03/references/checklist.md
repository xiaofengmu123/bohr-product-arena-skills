# 自检清单与降级规则（Checklist & Degradation）

收口前逐条勾选；未通过时按右侧动作处置。`scripts/evidence_map.py` 与 `scripts/selftest.py` 会分别自动核验 B/C/D 与 E/F 中的可判定项。

## A. 输出契约（可核验性）

- [ ] `/app/output/result.json` 存在且为合法 JSON（`json.load` 可读）
- [ ] 含 `case_id`，与 `/input/case.json` 一致
- [ ] 含 output_contract **全部**字段（原样回填，未知键不删不改名）
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log`
- [ ] `status` 与实际完成度一致（缺项用 `partial`，不虚标 `complete`）
- [ ] `change_log` 以 `seq/stage/note` 记录每次改动，不含墙钟时间

## B. 事实一致（写得实）

- [ ] 稿件是完整可编辑章节稿，不是提纲/写作建议/素材堆叠
- [ ] 每个数值/单位/样本量/显著性/图注都能指回 `material_index.json` 的 `locator` + 逐字 `quote`
- [ ] 正文没有凭领域常识补写、材料中不存在的事实；补写处已标 `[待确认]`
- [ ] 摘要与结果、正文与图注、方法与步骤之间无未说明冲突
- [ ] `evidence_map.py`：无悬空 `[S:]`；`review_numbers` 列表已逐条复核

## C. 引用对应（引得对）

- [ ] 每条 `[C:键]` 在参考文献区有定义，作者/年份/期刊/卷期/DOI 与给定文献一致
- [ ] 每条引用真正支撑所在句，无装饰性、无错配引用
- [ ] DOI/卷期缺失写 `unknown`，不杜撰；取不到全文时只依据给定元数据引用
- [ ] `evidence_map.py`：无悬空引用，`unused_citations` 为空或已说明理由

## D. 章节完整（全得起来）

- [ ] 覆盖题目/学科模板要求的摘要、引言、方法、结果、讨论、结论、参考文献、数据可得性声明等关键部分
- [ ] 图表、表格、声明字段齐备；缺项已显式标 `[缺失]` 并写入 `uncertainty`
- [ ] `evidence_map.py`：章节标题齐全；Figure/Table 编号无重复、无跳号

## E. 可编辑性（改得动）

- [ ] 稿件保留 Markdown 章节层级、图注编号、引用格式
- [ ] 可直接在 Word/Markdown 中继续修改并逐条复核
- [ ] 要求 docx/xlsx/pptx 时，已另渲染真实 PDF/PNG 预览供检查

## F. 确定性与稳定性（本版重点）

- [ ] `scripts/selftest.py` 退出码 0
- [ ] 同一 `/input` 两次运行，`result.json` 字节一致
- [ ] 空输入、坏 JSON、坏编码三种情况均退出码 0 且产出合法结果

## G. 降级策略（缺数据/缺软件时）

规则：**缺什么标什么，绝不编造。** 无法产出的部分写 `partial`，并在 `uncertainty`/`change_log` 说明 limitations。

| 情况 | 处置 |
|-|-|
| PDF 解析工具缺失/失败 | 用可用工具；失败则标 `[无法解析]`，不凭文件名/标签补写 |
| 图表缺图注 | 图注标 `[缺失]`，正文描述标 `[待确认]`，计入 `uncertainty` |
| 文献取不到全文 | 仅用给定元数据引用；缺失字段写 `unknown`，禁止补 DOI |
| 材料间数值/口径冲突 | 并列各方+来源，标 `[冲突待确认]`，写入 `sources[].conflict` |
| 材料超长 | 按可用部分产出 `partial`，说明未处理部分及原因 |
| 引用格式不认识 | 统一为 `[C:键] 作者. 题目. 期刊 年份;卷(期):页. DOI` 最小可核格式 |
| 脚本运行失败 | 保留已落盘骨架，手工补字段，如实记录到 `change_log` |

## H. 收口前最后三查

1. `result.json` 能被 `json.load` 正常读取；
2. `sources` 每条 `ref_id` 都能在材料索引或文献条目中找到；
3. `uncertainty` 覆盖全部 `[待确认]`/`[缺失]`/`[冲突待确认]`/`[无法解析]` 标记。
