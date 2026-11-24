---
name: research-stats-audit-ledger
description: >
  Audit a fixed manuscript for research design, variable definition,
  statistical methods, sample size/power, and results reporting under a
  900-second / 64-request budget. Writes a frozen, contract-first partial
  result skeleton in the first 3 minutes, then improves it incrementally
  through per-finding files and a machine-readable budget ledger. Every
  finding carries locator + verbatim quote + defect type + severity +
  evidence basis + revision suggestion. Use when the task asks for a
  reproducible research-methods and statistics review of a manuscript.
version: 1.0.0
---

# 研究方法与统计审查 Skill（预算账本版）

## 1. 角色与何时使用

你是一名研究方法与统计审查员，只做一件事：把一篇固定稿件审查成一份能交给作者逐条
回复的**审查单**——问题出在哪段/哪表/哪句、属哪类缺陷、严重到何种程度、依据是什么、
应该怎么改。适用：任务要求对固定论文/科研材料做研究设计、变量定义、统计方法、样本量
与检验效能、结果报告审查，并交付条目化、可复核的结论清单。

不做：文献检索与综述、全文翻译、语言润色、代写审稿结论、投稿决策、数据再建模。
本 Skill 只计四件事：问题发现、证据定位、严重度判断、修改建议。

判定基准、方法—数据类型匹配表、严重度分级见 `references/checklist.md`；
result.json 完整契约见 `references/result_schema.md`。执行前先完整读完这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明、`output_contract`（若有）。
- `/input/task.md`：任务与检查项说明。
- `/input/` 下材料（含 `materials/` 子目录）：论文/科研材料，常见 PDF，也可是
  txt/md/csv/json/docx。PDF 用真实 PDF 解析，无预抽答案层。

工作目录 `/app/work`，最终产物目录 `/app/output`。**以实际文件内容为准**，不要依据
目录名或领域标签假设材料内容。

脚本（相对路径，在包根 `/skills/weekly-skill` 下运行，纯 Python 标准库）：

- `scripts/main.py`：单一入口，子命令 `discover` / `scaffold` / `finalize` / `status`。
- `scripts/selftest.py`：端到端自检（正常样例 / 空输入 / 坏 JSON）。

`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。执行：
`python -B scripts/main.py discover`；如不在包根目录，用绝对路径
`/skills/weekly-skill/scripts/main.py`。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类
可读、可编辑）。result.json 顶层字段：

- `case_id`；`status` ∈ `ok|partial|insufficient_evidence|input_error`。
- `summary`（`verdict` + `counts` + `by_facet` + `by_type`）；`review_report.findings`
  （逐条问题）。
- `result_status`（`pass|problem|needs_review|unchecked`）；`evidence.evidence_map`
  （定位 + 逐字摘录 + 状态）。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
  `change_log`、`runtime`、`numeric_checks`、`progress`、`checked_classes`。

若 `case.json` 含 `output_contract`，其全部字段由 `scaffold` **原样回填**（缺值给
`null`/`[]`/`""`，不改名不删未知键），`finalize` 合并时保留所有未知顶层字段。
每条 finding 必填 `id/facet/locator/quote/severity/suggestion`，语义见 references。

`quote` 必须逐字摘自原文；`locator` 用可回查定位串（页/段/表/图）。`status` 要诚实：
材料不足写 `insufficient_evidence`，绝不编造数据、数值、引用或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束立即
刷新结果，任何中断都留下合法产物。控制请求次数，不回读已读文本。

本版采用**预算账本（budget ledger）**：`scripts/main.py status` 从
`/app/work/state.json` 读取已用秒数/请求数与各阶段状态，作为唯一预算事实来源；每完成
一次模型动作后更新 `state.json`，不要凭记忆估计预算。阶段预算如下：

| 阶段 | 时间预算 | 请求预算 | 动作 |
|---|---|---|---|
| 0 | 0–150s | ≤6 | 发现 + 骨架落盘 |
| 1 | 150–450s | ≤22 | 按检查面逐条审查 |
| 2 | 450–720s | ≤24 | 复核与交叉核对 |
| 3 | 720–900s | ≤12 | 自检与收尾 |

**阶段 0（0–150 秒，≤6 请求）——发现与骨架（硬性早落盘）**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py scaffold`，落盘 `/app/output/result.json` 骨架
   （status=`partial`、verdict=`unchecked`、findings=[]）与 `/app/work/state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单。
4. manifest 报错（缺 case.json/材料不可读/缺 pdftotext）→ 写 `/app/work/meta.json`
   的 `errors/degradations` 并重跑 `finalize`，进入降级，不崩溃。

**阶段 1（150–450 秒，≤22 请求）——按检查面逐段审查、逐条落盘**
1. 用 `/app/work/terms.json`（统计关键词命中）定位方法/结果段落；一次读 2–3 页/段，
   读完即审，不再回读。
2. 对照 `references/checklist.md` 五个检查面逐条找问题；每发现一条，立即写成
   `/app/work/findings/<id>.json`（一个文件一个对象，字段见 result_schema）。
3. 严格区分“做错了（factual_error）/没报告（insufficient_evidence）/不合规范
   （normative_issue）/建议（advisory）/待复核（pending_review）”，严重度匹配影响面。
4. 每完成一个检查面，运行一次 `python -B scripts/main.py finalize` 增量刷新
   result.json 与 report.md，并更新 `state.json` 该 facet 为 `done`。

**阶段 2（450–720 秒，≤24 请求）——复核与交叉核对**
1. 逐条回读 finding 对应原文，确认 quote 逐字、locator 准确、严重度与性质匹配。
2. 看 `finalize` 输出的 `numeric_checks` 候选（p 值与措辞/CI、多处 N/n 不一致），
   确证者提升为 finding，不确证者不写或写 `pending_review`。
3. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述；矛盾处两条证据都记录。
4. 无法确认的条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`。
5. 填 `/app/work/meta.json`：`status/overall_verdict/result_status/checked_facets/
   sources/uncertainty/limitations/...`，重跑 finalize。

**阶段 3（720–900 秒，≤12 请求）——收尾与自检**
1. 跑 `python -B scripts/selftest.py`（在其构造的临时样例上验证，不碰真实案例）。
2. 逐条核对 §5 自检清单；修正缺必填键、缺定位串、非逐字 quote。
3. 最终 `python -B scripts/main.py finalize`；确认 result.json 可 `json.load`、
   report.md 已生成、status/verdict 诚实。
4. 时间不足时保留当前 partial 产物，在 `limitations` 写明未完成检查项，绝不补造。

时间不够时的优先级：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位+摘录 →
状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 六必填齐全：id/facet/locator/quote/severity/suggestion；finding_type
  显式填写（事实错误/证据不足/规范/建议/待复核）。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录。
- [ ] 五个检查面（研究设计/变量定义/统计方法/样本量/结果报告）覆盖或显式标注未覆盖。
- [ ] 事实错误、证据不足、规范问题、建议、待复核五类已区分。
- [ ] 严重度与缺陷性质匹配：未把“没报告”写成硬伤，也未把硬伤写成建议。
- [ ] 方法判定区分“方法不当/可接受但报告不全/可接受可改”三档。
- [ ] `numeric_checks` 候选已逐条复核，确证者入 findings，其余不冒充结论。
- [ ] 冲突信息未隐藏；无法确认条目标 `needs_review` 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验来源；无虚构 DOI/URL/数值；status 诚实（空材料写
  `insufficient_evidence`）。
- [ ] 脚本纯标准库、`--help` 退出码 0、`selftest.py` 全绿、同一输入重复运行结果一致
  （除 `runtime` 时间戳外无随机数）。

## 6. 降级策略

遇以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，limitations 列出缺口 |
| 缺 case.json 且无材料 | status=`input_error`，errors 记录 |
| 坏 JSON/坏编码 | 记入 errors/degradations，跳过该文件继续读其它材料 |
| 缺 pdftotext | degradation 记录，该 PDF 标 `needs_ocr`，提示用 OCR 工具 |
| 扫描版 PDF（空文本） | 标 `needs_ocr`；无法识别则不判结论 |
| 仅摘要/原文取不到 | 相关结论标 `pending_review`，不硬判 |
| 来源版本不一致/严重歧义 | 两条都记录并标 `source_conflict`，进 uncertainty |
| 写盘失败 | 尝试降级到最小合法产物；仍失败写 stderr 并退出非 0 |
| 图表无法解析 | limitations 记录，正文可用则审正文 |

任何“缺失”都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源、撤回
状态、软件状态与评测分数。脚本子命令 `scaffold` 与 `finalize` 均保证在缺输入、坏 JSON
等情况下退出码为 0 并产出合法 result.json；只有写盘失败等无法落盘时才非 0 退出。
