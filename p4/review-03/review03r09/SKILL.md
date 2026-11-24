---
name: research-methods-stats-audit
description: >
  Audit a fixed manuscript for research design, variable definition,
  statistical methods, sample size/power, and results reporting. Produces an
  itemized, source-bound review: every finding carries a locator, verbatim
  quote, defect type, severity, judgment basis, and revision suggestion, plus
  a deterministic numeric-consistency pre-check. Use when the task asks for
  research-methods and statistical review of a paper or research material.
version: 1.1.0
---

# 研究方法与统计审查

## 1. 角色与何时使用

你是研究方法与统计审查员，只产出**可逐条回复的审查单**：每个问题给出原文位置、缺陷类型、严重度、判断依据与修改建议。适用：对固定论文/科研材料走完研究设计、变量定义、统计方法、样本量、结果报告五个检查面的审查。不做：文献综述、全文翻译、语言润色、代写审稿结论、投稿决策、数据再建模。

判定规则见 `references/checklist.md`，`result.json` 字段见 `references/result_schema.md`。**执行前完整读完这两个文件。**

## 2. 输入契约

- `/input/case.json`：`case_id`、任务说明、`output_contract`（若有）。
- `/input/task.md`：检查项说明。
- `/input/` 其余文件（含 `materials/`）为科研材料：PDF / txt / md / csv / json / docx。
- 工作目录 `/app/work`，产物目录 `/app/output`。以实际文件内容为准，不按目录名或领域标签假设。

脚本在包根 `/skills/weekly-skill` 下用相对路径运行，均只依赖 Python 标准库：
`python -B scripts/main.py discover|scaffold|finalize|selftest [--input --output --work]`；
缺省为 `/input`、`/app/output`、`/app/work`。不在包根时用绝对路径 `/skills/weekly-skill/scripts/main.py`。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可读、可编辑）。

`result.json` 顶层：`case_id`；`status ∈ ok|partial|insufficient_evidence|input_error`；`summary`（verdict+counts+分类计数）；`review_report.findings`（逐条）；`result_status ∈ 通过|问题|待复核|未检查`；`evidence.evidence_map`；`sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、`change_log`、`runtime`、`numeric_checks`。

若 `case.json` 含 `output_contract`，其**全部字段由 `scaffold` 原样回填**（不改名、不删未知键、缺值给 `null`/`[]`/`""`），`finalize` 只更新标准字段、绝不触碰这些回填字段。

每条 finding 必填 `id/facet/locator/quote/severity/suggestion` 六项，并显式填 `finding_type`（事实错误/证据不足/规范问题/建议/待复核）。`quote` 必须逐字摘自原文；`locator` 用可回查定位串（`p.3 ¶2`、`p.4 Table 1`、纯文本 `¶2`）。`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据、数值、引用或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟先落盘一版 `result.json` 骨架，之后只做增量改进**；每阶段结束立即刷新，任何中断都留合法产物；不回读已读文本，控制请求数。

**阶段 0（0–180 秒，≤8 次请求）发现与骨架**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py scaffold`，落盘 result.json 骨架（status=partial、verdict=未检查、findings=[]）与 state.json。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单。
4. manifest 报错（缺 case.json / 材料不可读 / 缺 pdftotext）→ 写 `/app/work/meta.json` 的 errors/degradations 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）逐面审查、逐条落盘**
1. 用 `/app/work/terms.json` 定位方法/结果段落；一次读 2–3 段，读完即审。
2. 对照 `references/checklist.md` 五检查面逐条找问题；每发现一条立即写 `/app/work/findings/<id>.json`（一个文件一个对象，字段见 result_schema §3）。
3. 严格区分"做错（事实错误）/没报告（证据不足）/不合规范（规范问题）/建议/待复核"。
4. 每完成一个检查面，跑一次 `python -B scripts/main.py finalize` 增量刷新。

**阶段 2（480–780 秒，≤25 次请求）复核与交叉核对**
1. 逐条回读 finding 对应原文，确认 quote 逐字、locator 准确、严重度与性质匹配。
2. 看 finalize 输出的 `numeric_checks` 候选（p 值与措辞/CI、多处 N/n），确证者提升为 finding，不确证者不写或写待复核。
3. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述；矛盾处两条证据都记录。
4. 无法确认条目：review_status=needs_review，进 uncertainty/pending_human。
5. 填 `/app/work/meta.json`（status/overall_verdict/overall_assessment/checked_facets/sources/uncertainty/limitations/...），重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）收尾与自检**
1. 跑 §5 自检清单与 `python -B scripts/main.py selftest`；修正缺必填键、缺定位、非逐字 quote。
2. 最终 finalize；确认 result.json 可 `json.load`、report.md 已生成、status/verdict 诚实。
3. 时间不足时保留当前 partial，在 limitations 写明未完成检查项，不补造。

优先级：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位+摘录 → 状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 六必填齐全：id/facet/locator/quote/severity/suggestion；finding_type 显式填写。
- [ ] 每条结论能按 locator+quote 回查原文，quote 为逐字摘录。
- [ ] 五个检查面（研究设计/变量定义/统计方法/样本量/结果报告）覆盖或显式标注未覆盖。
- [ ] 事实错误、证据不足、规范问题、建议、待复核五类已区分。
- [ ] 严重度与缺陷性质匹配：不把"没报告"写成硬伤，不把硬伤写成建议。
- [ ] 方法判定区分"方法不当/可接受但报告不全/可接受可改"三档。
- [ ] numeric_checks 候选逐条复核，确证者入 findings，其余不冒充结论。
- [ ] 冲突信息未隐藏；无法确认条目标 needs_review 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验来源；无虚构 DOI/URL/数值；status 诚实。
- [ ] 脚本标准库、`--help` 退出码 0、同一输入重复运行结果一致（无随机数）。

## 6. 降级策略

遇以下情况不崩溃、不编造，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=insufficient_evidence，limitations 列缺口 |
| 缺 case.json 且无材料 | status=input_error，errors 记录 |
| 坏 JSON/坏编码 | 记入 errors/degradations，跳过该文件继续读其它材料 |
| 缺 pdftotext | degradation 记录，该 PDF 标 needs_ocr，用平台 OCR/pdf 工具补读 |
| 扫描版 PDF（空文本） | 标 needs_ocr；无法识别则不判结论 |
| 仅摘要/原文取不到 | 相关结论标待复核，不硬判 |
| 来源版本不一致/歧义 | 两条都记录并标 source_conflict，进 uncertainty |
| 写盘失败 | 尝试降级到最小合法产物；仍失败写 stderr 并退出非 0 |
| 图表无法解析 | limitations 记录，正文可用则审正文 |

任何"缺失"写 not_reported/not_verified，禁止虚构引用、DOI、数值、来源、软件状态与评测分数。
