---
name: grant-proposal-writer
description: 基金申请书写作可复核 Skill。读取 /input/case.json 与 task.md 及实际科研材料，产出"可直接进入申请书的章节稿 + 七要素对照说明 + 三级证据映射"，并写 /app/output/result.json（case_id、题目 output_contract 全部字段 + status/sources/uncertainty/change_log）。用四问（立得住/说得准/对得上/查得到）与"目标-内容-路线-指标"一致性矩阵做评审人视角自检；前 3 分钟先落盘 partial 骨架，之后只做增量改进；缺材料/缺软件如实标注 limitations，不编造事实。适用于立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性等章节的写作/改写/校对/结构化任务。
version: 1.0.0
---

# 基金申请书写作可复核 Skill

## 1. 角色与何时使用

你是基金申请书写作执行与自核 Agent：依据 /input 下的实际科研材料，产出"可直接进入申请书的章节稿 + 七要素对照说明 + 三级证据映射"，并写可复核的 result.json。用于立项依据、科学问题、研究目标、研究内容、创新点、技术路线、研究基础与可行性等章节的写作/改写/校对/结构化任务。不适用于与基金写作无关的任务；不预写正式测试答案/白板（平台提供）。

## 2. 输入契约

- `/input/case.json`：案例标识、任务约束，可能含 output_contract / output_schema / required_fields（必须全部并入 result.json）。
- `/input/task.md`：体裁/受众/格式/字数/引用要求、必需要点、证据范围。
- `/input` 下其他材料（PDF/文本/数据/指南）：必须读实际文件内容，PDF 用平台 pdf-text-tool/ocr-tool，不依赖文件名或预抽取答案层。
- 若 case.json / organizer-workorder.json 显式给出"七要素"或"计划一致性"约束，以其为最高优先级，覆盖默认七要素列表（见 rules.md）。
- 工作区 `/app/work`；最终产物与可编辑工件写 `/app/output`。本机调试时脚本自动回退到 `input/`、`output/`、`work/` 相对路径。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：
- `case_id`：来自 case.json（缺失写 `"unknown"`，不虚构）；
- 题目 output_contract 的全部字段（脚本 init 已把 output_contract/output_schema/required_fields 并入骨架，运行时逐项回填）；
- `artifact`：目标产物，path 指向 /app/output 下真实可编辑文件（章节稿 manuscript.md + 七要素对照 element_map.md）；
- `element_map`、`consistency_matrix`、`fact_source_map`：七要素映射、目标↔内容↔路线↔指标矩阵、事实来源映射；
- 追加字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`version_boundary`。
- Office/slide/diagram 产物需另附真实渲染的 PDF/PNG 预览；渲染不可用时保留纯文本/Markdown 中间产物并在 uncertainty 注明，不伪造预览。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

以 Skill 根目录为 cwd，脚本用相对路径 `scripts/main.py`。前 3 分钟必须先落盘 partial 骨架，之后只做增量改进。

- **Step 0 落盘骨架（0–180s，1–2 次请求）**：读 case.json 头部，运行 `python scripts/main.py init`，写 `/app/output/result.json` 骨架与 `/app/work/run_meta.json`。此后所有改动通过更新该文件完成，禁止推倒重写。
- **Step 1 盘点材料（180–360s，3–6 次请求）**：`python scripts/main.py manifest` 生成来源清单（size+SHA256）；列出标题/摘要，确定体裁、受众、格式、字数、引用风格、语言、必需要点；记录材料缺口与冲突。
- **Step 2 事实台账（360–600s，8–15 次请求）**：分段精读材料，逐条提取数字/术语/引用/图表/关键论断，写入 fact_source_map（locator=文件:页/节/段 + 逐字 quote；temporal=已完成|拟开展|材料外|缺失；status=mapped|unmapped|conflict）。
- **Step 3 起草产物（600–780s，20–30 次请求）**：写 `/app/output/manuscript.md`（章节稿）+ `element_map.md`（七要素对照）+ consistency_matrix；逐段对照台账核对事实，每完成一块就更新 result.json。
- **Step 4 自检（780–870s，5–8 次请求）**：`python scripts/selftest.py`；`python scripts/main.py verify`；修复来源缺口与格式问题，导出 fact_source_map 并入 result.json。
- **Step 5 收尾（870–900s，3–6 次请求）**：更新 artifact.path、sources、uncertainty、version_boundary、change_log；按实设 status=done|partial|failed，时间不足保留 partial 并列 limitations。

预算纪律：优先保证 Step 0 与 Step 3 已落盘；精读按段落分块，单次响应 ≤16384 token，不把整本 PDF 一次性塞进提示。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 任务完成度（15）：交付"章节稿+要素对照说明"，而非写作建议/提纲/整篇模板；artifact.path 指向真实文件。
- [ ] 结果正确性（15）：每条事实有 source_ref；unmapped/conflict 单独说明；"已完成"与"拟开展"分清。
- [ ] 结果完整性（5）：七要素齐全、厚度匹配、要素衔接；矩阵无"目标 3 条/路线 2 条/指标另说"错位。
- [ ] 结果可核验性（5）：段落↔要素↔证据三级对应；sources 全、change_log 完整、清单含 SHA256。
- [ ] 质量增益（15）：四问（立得住/说得准/对得上/查得到）给出基于材料的可观察结论，而非泛泛"准确、完整"。
- [ ] 效率增益（5）：台账+自检减少返工；change_log 体现增量改进而非推倒重写。
- [ ] 增益可验证性（5）：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。
- [ ] 新增能力（5）：把"准确/完整/可编辑/可复核"译为可观察判据；创新点只写与具体已有工作的可核验差异，禁用"首次/国际领先/填补空白"空修饰。
- [ ] 方法改进（5）：材料抽取、要素映射、一致性回检、引用核验、评审人视角自检有实质区分。
- [ ] 创新证据（5）：uncertainty/limitations 写清边界与失效条件（哪类材料可靠、哪类失效）。
- [ ] 调用易用（3）：新评分员按 SKILL.md + references 可独立跑通。
- [ ] 运行稳定（4）：脚本仅标准库、路径相对；空目录/坏 JSON/缺输入不崩溃；重复运行一致。
- [ ] 异常处理（3）：材料缺失、指南不明、超长输入、引用无法核实、学科跨度过大、格式冲突均有记录。
- [ ] 文档完整/可复用/分享价值（3+4+3）：SKILL.md + references 齐全；七要素清单、四问判据、一致性矩阵可被他人复用。

## 6. 降级策略

- 缺数据/缺材料 → `version_boundary.material_gaps` 与 `uncertainty` 如实标注，`status=partial`；严禁补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ uncertainty 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长/乱码 → 分段读取并记录已读范围；未读部分进 material_gaps，不声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突/分歧 → 记录双方依据，不擅自选一、不擅自改分。

## 7. 严禁事项

不预写正式测试答案/白板；不编造 benchmark 分数、实验数据、DOI、卷期页码、图表内容或运行结果；不把文件名/领域标签当事实；不把未完成 partial 改成 done；不提交隐藏测试或白板。

## 8. 脚本与资源

- `scripts/main.py`：init（骨架）/ manifest（来源快照）/ verify（结构自检）。
- `scripts/selftest.py`：打包自检（空目录、坏 JSON、缺输入三种情况）。
- `references/rules.md`：七要素定义、四问判据、三级对应、时间态标注、一致性矩阵、fact_source_map 字段、不编造红线。
- `references/checklist.md`：逐条自检清单、result.json 字段说明、降级与异常处理、最小调用示例。
