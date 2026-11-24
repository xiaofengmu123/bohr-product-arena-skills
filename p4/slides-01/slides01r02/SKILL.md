---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill。在 900 秒 / 64 次模型请求预算内，把论文重写为可讲、可核验、可编辑的学术演示：先读材料抽主线与页级主张，再真继承模板母版/版式成稿，逐页保留 claim/role/evidence 与 locator+quote，导出页数一致的 PDF 并自检。适用于学术汇报、组会、答辩类演示，不负责论文精读结论与科研绘图美化。
version: 0.5.0-budget
---

# 论文转学术 PPT（slides-01）

## 1. 角色与何时使用
你是"学术演示结构师"：把一篇论文/研究材料重写为**页级离散表达**——一页一个主张，主张挂论文里的图/表/章节/数据，再套用给定模板成稿。这不是把段落切块贴页，而是一次结构重写。
- 何时用：输入含论文 PDF/汇报稿/大纲 + 模板 pptx，要求产出可放映、可编辑、可回溯的学术演示。
- 边界：不生成论文精读结论本身、不做科研绘图美化、不做学术润色；相邻课题产物可作输入，本 Skill 只对"论文→可讲可核验可编辑 PPT"这条链路负责。

## 2. 输入契约
- 权威事实来源：`/input/case.json`（`case_id`、`output_contract` 字段名以它为准）+ `/input/task.md`；材料在 `/input` 下（PDF/pptx/图片/文本等）。
- 实际输入与原论文是唯一事实依据；PDF 只按真实文件解析，不依据错误的领域标签补造数据。
- 工作目录 `/app/work`；最终产物一律写 `/app/output/`（含 `result.json`）。
- 起手必读：`cat /input/case.json`；`ls -R /input | head -100`（先发现，再假设，不假设固定文件名/键名）。

## 3. 输出契约（硬要求）
必须产出 `/app/output/result.json`，且满足：
1. `case_id` 从 `/input/case.json` 原样复制；
2. 回填 `output_contract` 的**全部字段**（缺值给 `null`/`[]`/`{}`，不改名、不删未知键）；
3. 额外带信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（每次重写追加一条：`{time, scope, why}`）；
4. 主产物：真实可编辑 `presentation.pptx` + 页数一致 `presentation.pdf`；`editable_source`/`export_artifact` 指向真实存在的文件，页数填实际值；
5. `content_map`：每页一项 `{page, claim, role, evidence:{locator, quote}}`，`quote` 逐字命中材料；读不出的写 `uncertainty`/`pending_human`，绝不编造。
> 无 pptx 能力时给真实 SVG+PNG 并写 `exceptions`。**没有 result.json 才是 0 分**；读不到输入也要交 `status="input_error"` 的骨架（`case_id="unknown-case"`）。

## 4. 执行流程与阶段预算（900s / 64 请求；先落盘再改进）
总原则：**前 3 分钟必落盘可交付骨架，之后只做增量改进，永不推倒重来。** 每个阶段结束 = 原子重写一次 `result.json` + 追加 `change_log`。脚本只做机械脚手架，内容组织与写作由你真实读材料后完成。

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 | 阶段门禁 |
|---|---|---|---|---|
| P0 落地骨架 | 0:00–3:00 | ≤6 | case.json/task.md → `result.json` 骨架 + `/app/work/plan.md`（标题/背景/方法/结果/讨论/局限/结论七个证据槽位） | result.json 存在且为合法 JSON |
| P1 读材料·抽主线 | 3:00–7:00 | ≤16 | 论文/材料 → `/app/work/draft.json`（页级 claim/role/evidence，每项带 locator+quote） | 每页主张都有证据定位，读不出写缺口 |
| P2 成稿·继承模板 | 7:00–11:30 | ≤22 | draft + 模板 → `presentation.pptx`（真继承母版/版式，禁"空白+贴文本框"）+ 讲稿备注（不与页面文字重复） | pptx 可打开、页数 ≤ 上限 |
| P3 导出·自检 | 11:30–13:30 | ≤12 | pptx → `presentation.pdf` + `report.md`，跑 `python3 scripts/selftest.py`，回填 `compliance_checks` | PDF 页数与 pptx 一致 |
| P4 增量补全 | 13:30–15:00 | ≤8 | 只改缺口/渲染问题，逐条补 `content_map`/`sources` | 最终 status 诚实（ok/partial/insufficient_evidence/input_error） |

- 时间纪律：P0 到 3 分钟无论读到什么都先写骨架；连续思考 ≤ 90 秒；剩 5 分钟必须已在 P3/P4 且已落盘。
- 请求纪律：每阶段按预算消费，接近上限即进入"只读补充模式"（只改已落盘文件，不开新长流程）。
- 骨架不是空占位：P0 就写好七槽位页级计划骨架 + 证据槽位，超时也能交出一份主线可见、缺口可查的结果。

## 5. 方法要点（对应六类要素）
1. **主线提取**：识别问题/方法/关键证据/结论，页序按叙事逻辑，区分主结论与支撑细节。
2. **信息分层**：一页一个主张，长段落改写为短语，控单页密度；长句不塞窄框。
3. **证据保留**：图表编号/单位/图注与正文引用对得上；示意图不当实验数据；数值单位不改写。
4. **讲稿备注**：与页面文字分离，写过渡、强调点、被问时的补充证据。
5. **模板约束**：在模板占位符/字号/配色/页脚规则内排布，长标题、多图、大表格降级处理。
6. **导出渲染**：换机/导出后字体图形项目符号不崩，源文件逐页可编辑。

## 6. 自检清单（交付前逐条勾选）
- [ ] `result.json` 是合法 JSON，含 `case_id` 与 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log
- [ ] `presentation.pptx` 真实存在、可打开、真继承模板母版/版式（非"空白+贴文本框"伪继承）
- [ ] `presentation.pdf` 存在且页数 == pptx 页数（`python3 scripts/main.py --stats` 核对）
- [ ] 每页 `content_map` 有 claim/role/evidence 且 quote 逐字命中材料
- [ ] 图表编号、单位、方法名、结论与论文一致，无张冠李戴
- [ ] 讲稿备注与页面文字不重复，含过渡与问答要点
- [ ] 缺数据/缺软件处已在 `exceptions`/`uncertainty`/`pending_human` 如实标注
- [ ] `python3 scripts/selftest.py` 退出码 0
- [ ] 未编造任何分数、DOI、数值、运行结果

## 7. 降级策略（如实标注，不编造）
- 缺输入/坏 JSON/坏编码 → 仍写合法骨架，`status="input_error"` 或部分字段，`errors`/`degradations` 记录，继续处理其余材料，不崩溃。
- 图表无法提取、公式为图片、PDF 加密、模板占位符不足、字体缺失、页数超限 → 对应写 `exceptions`/`pending_human`，交付能做的最优产物并说明限制。
- 缺 pptx 软件 → 给真实 SVG+PNG 替代并写 `exceptions`；缺渲染工具 → PDF 缺失也要在 `export_artifact` 写 `pages:null` 与原因。
- 材料不足 → `status="insufficient_evidence"`，列出缺口；**绝不示例填充、绝不编造数据**。

## 8. 脚本（相对路径，仅标准库）
- `python3 scripts/main.py --help`：用法；默认跑脚手架（发现材料 → 写 result.json 骨架 → 校验）。
- `python3 scripts/main.py --validate`：校验 result.json 契约与产物存在性，退出码 0/1。
- `python3 scripts/main.py --stats`：材料清单 + pptx/pdf 页数 + 模板母版/版式数（stdlib 解析，页数为启发式估计）。
- `python3 scripts/selftest.py`：空目录 / 坏 JSON / 缺输入 / 正常四场景自检，退出码 0/1。
> 脚本是可选单次工具，不串流水线；内容组织与写作由你完成。

## 9. 交付前最终检查
1) result.json 合法且字段齐；2) pptx/pdf 页数一致；3) report.md 记录方法/取舍/限制；4) change_log 非空且能还原每次改动；5) selftest 通过；6) 未编造分数、DOI、数值或运行结果。
