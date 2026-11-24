---
name: slides-01
description: 论文转学术 PPT（slides-01）Skill：先把论文组织成页级计划（每页一个主张 + 图/表/章节定位 + 逐字证据），再按给定模板成稿。要求每页 claim/role/evidence 三要素、图表编号与正文引用对得上、讲解备注与页面正文分离、真继承模板母版/版式/主题，并交付可编辑 pptx + 页数一致 PDF。适合“把论文变成可讲、可核验、可编辑的学术汇报”这一固定链路。
version: 1.0.0
---

# 论文转学术 PPT（slides-01）· 900 秒内交付的最短路径

## 0. 先看这 10 行（照做，不要先跑长流程）
1) `mkdir -p /app/work /app/output`；
2) `cat /input/case.json`，记下 `case_id` 与 `output_contract` 的字段名（以它为准）；
3) **立刻** `python3 -B scripts/main.py --mode init` 落盘 `/app/output/result.json` 骨架；
4) 每填一部分就**重写一次** `result.json`；关键字段带 `locator` + 逐字 `quote`；
5) 最后补 `presentation.pptx` + 页数一致 `presentation.pdf`，并 `--mode check` 自检。
- **时间纪律：每 40 秒至少写一次文件；连续思考 ≤2 分钟；剩 5 分钟必须已全部落盘。**
- 预算 64 请求 / 900 秒 / 16384 tokens；**先交粗稿再改进**，不要一遍追求完美。

## 1. 角色与何时使用
把一篇论文/研究材料**结构重写**成可讲的学术 PPT：先抽主线（问题→方法→证据→结论），再逐页
给主张、角色、证据、图/表定位与讲解备注，最后在给定模板内成稿并导出可复核的 PDF。
**不是**“把摘要和正文段落切块贴进页面”。使用条件：已有论文或研究材料 + 目标听众/时长/页数要求；
没有材料时按 §6 降级交付，不做示例填充。

## 2. 输入契约（先发现，再假设）
- 权威契约：`/input/case.json` 的 `case_id` + `output_contract`（**字段名以它为准**，不改名、不删未知字段）；
- 任务说明：`/input/task.md`；材料：论文 PDF/汇报稿/大纲、模板 pptx、图表（可能平铺或在 `materials/`）；
- 起手：`cat /input/case.json`；`ls -R /input | head -100`。实际 PDF 用平台工具（pdftotext/OCR）读原文，
  **不依据文件名或领域标签猜内容**；PDF 加密/无文本层走 §6 降级。

## 3. 输出契约（写 `/app/output/`，必含 result.json）
`result.json` 必须：① 原样回填 `output_contract` 全部字段（缺值 `null`/`[]`/`{}`）；② 含 `case_id` 与
`status`（`ok | partial | insufficient_evidence | input_error`）；③ 含统一信封 `sources` / `uncertainty` /
`pending_human` / `errors` / `degradations` / `change_log` / `runtime`。
`output_contract` 缺失时回退规范字段：`editable_source`、`evidence_map`、`render_record`、
`compliance_checks`、`exceptions`（详见 `references/contract.md`）。
主产物：真实 `presentation.pptx` + 页数一致 `presentation.pdf`（无 pptx 能力给真实 SVG+PNG 并写 `exceptions`）。

## 4. 执行流程（分步骤，先骨架后增量）
| 步 | 动作 | 输入 → 输出 | 预算 |
|---|---|---|---|
| 1 | 读 case.json，记 case_id + 契约字段名 | case.json → 契约清单 | ≤1 min |
| 2 | `scripts/main.py --mode init` | 契约 → result.json 骨架 + work/plan.json | ≤1 min（**前 3 min 必须完成**） |
| 3 | 读材料，抽主线：问题/方法/关键证据/结论/局限 | 论文 → 主线骨架 | ~3 min |
| 4 | 写 `work/draft.json`：每页 `{page, claim, role, evidence:[{locator,quote}], figures, notes}` | 主线 → 页级计划 | ~4 min |
| 5 | 按模板成稿：真继承母版/版式/主题，写 pptx + 讲解备注(notes.md 或备注窗格) | draft → pptx | ~3 min |
| 6 | 导出 PDF，核对页数一致；`scripts/main.py --mode check` | pptx → PDF + compliance_checks | ~2 min |
| 7 | 余时增量改进：补证据、修版面、补备注 | 自检清单 → 修订 | 剩 2 min 兜底 |

- **步骤 2 之前不许写正文**；`draft.json` 是单一事实源，没读出的页写缺口到 `uncertainty`/`pending_human`。
- 每完成一步重写一次 `result.json`；`scripts/` 只作单次工具，**不串成流水线**。

## 5. 评分维度对齐（每条执行动作都应能指回某个评分点）
- **实际效果 40**：完成度=主线重写而非段落搬家；正确性=数值/单位/图表编号逐字核对；完整性=必需页齐全+
  六要素覆盖；可核验=每页 locator+quote、交付 pptx+PDF+plan.json。
- **使用增益 25**：质量=同模板/页数上限内可复现版式；效率=900s/64 请求内交粗稿再改进；可验证=runtime
  记录开始/结束/软件版本，不编造对照结果。
- **创新增益 15**：新增能力=页级引用与图表定位；方法改进=备注与页面正文分离生成；创新证据=写明与
  “通用 PPT 工具/手工排版”的边界（见 `references/rubric-checklist.md`）。
- **可靠体验 10**：易用=脚本 `--help` 退出码 0、相对路径可跑；稳定=三态不崩、重复运行一致；异常=如实降级。
- **生态贡献 10**：文档=SKILL.md+references 齐备；可复用=页级大纲模板+证据映射格式；推广=自检清单与降级规则可迁移。
> 完整“维度→动作→自检项”映射见 `references/rubric-checklist.md`；未覆盖的评分点就是本 Skill 的已知短板，如实写在 `degradations`。

## 6. 降级策略（缺数据/缺软件如实标注，不编造）
- 缺/坏 `case.json`：仍写 result.json，`status="input_error"`、`case_id="unknown-case"`、`errors` 记原因。
- 图表无法提取/公式为图片/PDF 加密/字体缺失/页数超限：写 `exceptions` + `degradations`，用页面文字或占位替代并
  标注“需人工补齐”；**绝不编造数值、引用、DOI**。
- 无 pptx 能力：给真实 SVG+PNG，`exceptions` 说明可编辑源缺失；给定模板必须真继承，禁止伪继承。
- 证据冲突：两来源不一致都记录并标 `source_conflict`；材料未报告写 `uncertainty`/`pending_human`。

## 7. 自检清单（交付前逐条勾选；未勾选项写进 exceptions/degradations）
- □ 契约字段：result.json 含 case_id + output_contract 全部字段 + 统一信封
- □ 状态诚实：status 与实际产物匹配（缺证据→partial/insufficient_evidence，不写 ok）
- □ 主线：页序构成“问题→方法→证据→结论”，无段落搬家
- □ 证据：每页 claim/role + evidence[{locator,quote}]，quote 逐字命中材料
- □ 正确性：数值/单位/图表编号与材料一致，示意图未冒充实验数据
- □ 备注：每页 notes 非空且不重复页面正文，含过渡/追问点
- □ 模板：真继承母版/版式/主题，未溢出、未覆盖页脚配色
- □ 渲染：presentation.pdf 页数与 slides 数一致；`scripts/main.py --mode check` 通过
- □ 健壮：`scripts/main.py --mode selftest` 退出码 0；三态（空/坏 JSON/缺输入）不崩
- □ 可交接：文件数 5–8 个、总体积 ≤140KB，无占位空文件

## 8. 脚本与资源（相对包根目录调用；若 cwd 不在包根，先 `cd` 到 Skill 根目录）
- `scripts/main.py --mode init|check|verify`（纯标准库）：契约发现、骨架落盘、结果校验、文件级验证；
- `scripts/pptx_oo.py`：无 python-pptx/无模板时的最小可编辑 pptx 兜底（标 degradations）；
- `scripts/main.py --mode selftest`：提交前自检（空输入/坏 JSON/动态契约/pptx 构建/check 不崩）；
- `references/contract.md`：输入输出契约 + 降级规则；`references/rubric-checklist.md`：评分维度对齐表。
