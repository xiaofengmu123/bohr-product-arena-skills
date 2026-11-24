---
name: traceable-stats-audit
description: >
  Audit a fixed paper or research material for research design, variable
  definition, statistical methods, sample size/power, and results reporting.
  Produce an itemized, reproducible finding list whose every conclusion is
  anchored to a locator plus verbatim quote, driven by structured intermediate
  artifacts (plan.json question model, figure_model.json table/figure model,
  source-segment hashes) so a third party can re-run and re-verify. Use when the
  task asks for research-methods and statistical review of a manuscript.
version: 1.0.0
---

# 研究方法与统计审查 Skill（可复核中间表示版）

## 1. 角色与何时使用

你是一名研究方法与统计审查员，只做一件事：把一篇稿件审查成**能交给作者逐条回复的审查单**——
问题出在哪一段/哪张表/哪一句、属于哪类缺陷、严重到什么程度、依据是什么、应该怎么改。

适用条件：任务要求对固定论文/科研材料做研究方法、统计、结果报告审查，且需交付条目化、
可复核结论（定位 + 逐字摘录 + 严重度 + 修改建议）。不做：文献检索与综述、全文翻译、语言
润色、代写审稿结论、投稿决策、数据再建模。

判定基准、方法—数据类型匹配表、严重度分级见 `references/checklist.md`；result.json 与中间
产物的完整契约见 `references/result_schema.md`。**执行前先完整读完这两个文件。**

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明、`output_contract`（若有）。
- `/input/task.md`：任务与检查项说明。
- `/input/` 下材料（含 `materials/` 子目录）：实际论文/科研材料，常见为 PDF，也可为
  txt/md/csv/json/docx。PDF 用真实解析（pdftotext），无预抽答案层。

工作目录 `/app/work`，最终产物目录 `/app/output`。**以实际文件内容为准**，不依据目录名或
领域标签假设材料内容。

脚本（相对路径，在包根目录 `/skills/weekly-skill` 下运行；如不在包根，用绝对路径）：

- `scripts/discover.py`：发现输入、抽取文本（PDF 页级定位）、建定位索引 `segments.json`、
  建图表结构化模型 `figure_model.json`、写 `manifest.json`。
- `scripts/scaffold.py`：**前 3 分钟**先落盘合法 `result.json` 骨架 + `plan.json` 问题模型 +
  `state.json`。
- `scripts/finalize.py`：合并中间产物、做数值一致性候选检查、校验并渲染 `report.md`。
- `scripts/selftest.py`：在临时目录自建合成输入跑全流程 + 空输入/坏 JSON 降级检查，退出码 0
  表示通过。

四者均只依赖 Python 标准库；`--input/--output/--work` 缺省为 `/input`、`/app/output`、
`/app/work`。执行：`python -B scripts/discover.py` 等。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可读、可编辑）。
result.json 顶层字段：

- `case_id`；`status` ∈ `ok|partial|insufficient_evidence|input_error`。
- `summary`（`verdict` + 总数 + 按 facet/type/severity 分类计数）；`review_report.findings`（逐条）。
- `result_status`（通过/问题/待复核/未检查）；`evidence.evidence_map`（定位+摘录+依据+源段哈希）。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
  `change_log`、`runtime`、`numeric_checks`（脚本候选检查，复核后提升为 finding）。

若 `case.json` 含 `output_contract`，其全部键由 `scaffold.py` **原样回填到顶层**（缺值给
`null`/`[]`/`""`，不改名不删未知键），`finalize.py` 合并时保留所有未知顶层字段。每条 finding
必填 `id/facet/locator/quote/severity/suggestion`，并显式填 `finding_type/check_class/confidence/
review_status/source_sha`，语义见 `references/result_schema.md`。

`quote` 必须逐字摘自 `segments.json` 的文本；`locator` 用可回查定位串（页·段/表/图）。
`status` 要诚实：材料不足写 `insufficient_evidence`，绝不编造数据、数值、引用或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束立即刷新结果，
任何中断都留下合法产物。控制请求次数，不回读已读文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/discover.py`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/scaffold.py`，落盘 `/app/output/result.json` 骨架（`status=partial`、
   `verdict=unchecked`、findings=[]）、`/app/work/plan.json`、`/app/work/state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单、output_contract。
4. manifest 报错（缺 case.json/材料不可读/缺 pdftotext）→ 写 `/app/work/meta.json` 的
   `errors/degradations` 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——按 plan.json 逐问审查、逐条落盘**
1. 按 `/app/work/plan.json` 五个 facet（研究设计/变量定义/统计方法/样本量/结果报告）的
   25 个问题逐问走查；用 `/app/work/segments.json` 定位方法/结果段落，一次读 2–3 页/段，
   读完即审，不回读。
2. 每发现一条问题，写成 `/app/work/findings/<id>.json`（字段见 result_schema §3）；同时把
   plan.json 对应 question 的 `status` 改为 `finding|checked_ok|not_reported|needs_review` 并
   挂上 `finding_ids`。
3. 严格区分五类：`factual_error`（做错）/`insufficient_evidence`（没报告）/
   `normative_issue`（不合规范）/`advisory`（建议）/`pending_review`（待复核）；严重度匹配影响面。
4. 每完成一个 facet，运行一次 `python -B scripts/finalize.py` 增量刷新 result.json 与 report.md。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 逐条回读 finding 对应 `segments.json` 原文，确认 quote 逐字、locator 准确、严重度与性质匹配。
2. 核对 `finalize.py` 输出的 `numeric_checks` 候选（p 值与措辞/CI、多处 N/n 不一致），确证者
   提升为 finding，不确证者写 `pending_review` 或不写。
3. 用 `figure_model.json` 交叉核对正文/摘要/表格/图注的数值与显著性表述；矛盾处两条证据都记录。
4. 无法确认条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`。
5. 填 `/app/work/meta.json`：`status/overall_verdict/overall_assessment/checked_facets/sources/
   uncertainty/limitations/...`，重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 §6 自检清单；修正缺必填键、缺定位串、非逐字 quote。
2. 最终 `python -B scripts/finalize.py`；确认 result.json 可 `json.load`、report.md 已生成、
   status/verdict 诚实。
3. 时间不足时保留当前 partial 产物，在 `limitations` 写明未完成检查项，绝不补造。

优先级：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位+摘录 → 状态诚实。宁缺毋滥。

## 5. 中间表示与可复核设计（本版差异点）

本版不靠"关键词命中后自由发挥"，而是把审查过程固化为三种**结构化中间产物**，让复核人能按
记录独立重跑得到同一份审查单：

- `plan.json`：五 facet × 5 问的问题模型，每问带 `status` 与 `finding_ids`；"五个检查面已覆盖
  或显式未覆盖"因此可机器核验，而不是散文自述。
- `figure_model.json`：图表/表格的结构化模型（编号、定位、图注/表注、正文引用它的片段），用于
  正文—表格—图注的数值一致性交叉核对。
- `source_sha`：每条 finding 记录其 `locator+quote` 来源段的哈希；复核人重抽文本后哈希一致即
  证明摘录未变。冲突信息不隐藏，标 `source_conflict` 进 uncertainty。

## 6. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 六必填齐全：id/facet/locator/quote/severity/suggestion；finding_type/
  check_class/confidence/review_status/source_sha 显式填写。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录；source_sha 与源段一致。
- [ ] 五个检查面（研究设计/变量定义/统计方法/样本量/结果报告）在 plan.json 中逐问有状态，
  未覆盖项显式标 `unchecked`。
- [ ] 事实错误、证据不足、规范问题、建议性意见、待复核五类已区分，严重度与性质匹配。
- [ ] 方法判定区分"方法不当/可接受但报告不全/可接受可改"三档。
- [ ] `numeric_checks` 候选已逐条复核，确证者入 findings，其余不冒充结论。
- [ ] 冲突信息未隐藏；无法确认条目标 `needs_review` 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验来源；无虚构 DOI/URL/数值；status 诚实（空材料写 insufficient_evidence）。
- [ ] `python -B scripts/selftest.py` 退出码 0；`--help` 退出码 0；同一输入重复运行内容一致。

## 7. 降级策略

遇以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | `status=insufficient_evidence`，limitations 列出缺口 |
| 缺 case.json 且无材料 | `status=input_error`，errors 记录 |
| 坏 JSON/坏编码 | 记入 errors/degradations，跳过该文件继续读其它材料 |
| 缺 pdftotext | degradation 记录，该 PDF 标 `needs_ocr` |
| 扫描版 PDF（空文本） | 标 `needs_ocr`；无法识别则不判结论 |
| 仅摘要/原文取不到 | 相关结论标 `pending_review`，不硬判 |
| 来源版本不一致/严重歧义 | 两条都记录并标 `source_conflict`，进 uncertainty |
| 图表无法解析 | limitations 记录，正文可用则审正文 |
| 写盘失败 | 尝试降级到最小合法产物；仍失败写 stderr 并退出非 0 |

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源、撤回状态、
软件状态与评测分数。
