---
name: research-methods-stats-audit
description: >
  Audit a fixed paper or research material across research design, variable
  definition, statistical methods, sample size/power, and results reporting.
  Produce an itemized, reproducible review list where every finding carries a
  locator, verbatim quote, defect type, severity, judgment basis, and revision
  suggestion, backed by a deterministic numeric-consistency pre-check and a
  scoring-dimension-to-self-check traceability matrix. Use when the task asks
  for research-methods and statistical review of a manuscript.
version: 2.0.0
---

# 研究方法与统计审查 Skill（评分维度对齐版）

## 1. 角色与何时使用

你是一名研究方法与统计审查员，唯一目标是把一篇稿件变成一份可交作者逐条回复的**审查单**：
每条意见指出问题在**哪一节/哪张表/哪一句**、属于**哪类缺陷**、严重到什么程度、**依据是什么**、
应该怎么改。适用：任务要求对固定论文/科研材料做研究方法、统计、结果报告审查，且需要条目化、
可复核的结论清单。不做：文献综述、全文翻译、语言润色、代写结论、投稿决策、数据再建模。

判定基准见 `references/checklist.md`；result.json 契约见 `references/result_schema.md`。
执行前先完整读完这两个文件与 `scripts/main.py` 的三个子命令说明。

## 2. 设计取舍：评分维度 → 流程步骤 → 自检项 一一对齐

本版与通用审查 Skill 的区别：不把评分维度当目标口号，而是把它**逐条下沉为流程动作和自检项**。
执行时按 §4 流程走，收尾按 §6 清单勾选；每条自检项都标注它服务的评分维度（见括号）。

| 评分维度（分值） | 对应流程动作 | 对应自检项 |
|---|---|---|
| 实际效果·完成度/正确性（30） | §4 阶段1 五个 facet 逐面走查并逐条落盘 | §6 A1–A4 |
| 实际效果·完整性（5） | §4 阶段1 每面结束即 finalize，六类 check_class 覆盖登记 | §6 A5–A6 |
| 实际效果·可核验性（5） | §4 阶段2 quote/locator 逐条回原文复核 | §6 A7–A9 |
| 使用增益（25） | 骨架先行 + 增量 finalize + 脚本候选检查，减少回查与返工 | §6 B1–B3 |
| 创新增益（15） | 五类缺陷分离 + 数值一致性候选兜底 | §6 C1–C2 |
| 可靠体验（10） | §7 降级策略 + §8 边界 + 脚本确定性 | §6 D1–D4 |
| 生态贡献（10） | checklist/result_schema 独立成文，脚本纯标准库可复用 | §6 E1–E2 |

## 3. 输入契约

运行时固定输入：

- `/input/case.json`：`case_id`、任务说明、`output_contract`（若有）。
- `/input/task.md`：任务与检查项说明。
- `/input/` 下材料（含 `materials/` 子目录）：实际论文/科研材料，常见为 PDF，也可是
  txt/md/csv/json/docx。PDF 用真实 PDF 解析，**无预抽答案层**；扫描版标 `needs_ocr`。

工作目录 `/app/work`，最终产物目录 `/app/output`。**以实际文件内容为准**，不依据目录名
或领域标签假设材料内容。

脚本（在包根目录 `/skills/weekly-skill` 下，相对路径调用）：

```text
python -B scripts/main.py discover      # 发现输入、抽文本、建定位索引
python -B scripts/main.py scaffold      # 落盘合法 result.json 骨架
python -B scripts/main.py finalize      # 合并 findings、自检、渲染 report.md
python -B scripts/selftest.py           # 交付前自检
```

三者缺省 `--input/--output/--work` 为 `/input`、`/app/output`、`/app/work`；均只依赖
Python 标准库。不在包根时用绝对路径 `/skills/weekly-skill/scripts/main.py`。

## 4. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可读、可编辑）。
`result.json` 顶层字段：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`。
- `summary`（`verdict` + `counts` + 分类计数）；`review_report.findings`（逐条问题）。
- `result_status`（通过/问题/待复核/未检查）；`evidence.evidence_map`（定位 + 摘录 + 依据）。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
  `change_log`、`runtime`、`numeric_checks`（脚本自动候选检查）。

若 `case.json` 含 `output_contract`，其全部字段由 `scaffold` **原样回填到顶层**（缺值给
`null`/`[]`/`""`，不改名、不删未知键），`finalize` 合并时保留所有未知顶层字段。
每条 finding 必填 `id/facet/locator/quote/severity/suggestion`，另必填 `finding_type`、
`check_class`、`confidence`、`review_status`，语义见 references。

`quote` 必须逐字摘自原文；`locator` 用可回查定位串（`p.3 ¶2` / `p.4 Table 1` / `¶5`）。
`status` 要诚实：材料不足写 `insufficient_evidence`，绝不编造数据、数值、引用或结论。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束立即刷新结果，
任何中断都留下合法产物。控制请求次数，不回读已读文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py scaffold`，落盘 `/app/output/result.json` 骨架
   （status=`partial`、verdict=`unchecked`、findings=[]）与 `state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单。
4. manifest 报错（缺 case.json / 材料不可读 / 缺 pdftotext）→ 记入 meta.json 的
   `errors/degradations` 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——按检查面逐段审查、逐条落盘**
1. 用 `/app/work/terms.json`（统计关键词命中）定位方法/结果段落；一次读 2–3 页/段，
   读完即审，不再回读。
2. 对照 `references/checklist.md` 五个检查面逐条找问题；每发现一条，立即写成
   `/app/work/findings/<id>.json`（一个文件一个对象，字段见 result_schema §2）。
3. 严格区分"做错了（factual_error）/没报告（insufficient_evidence）/不合规范
   （normative_issue）/建议（advisory）/待复核（pending_review）"，严重度匹配影响面。
4. 每完成一个检查面，运行一次 `python -B scripts/main.py finalize` 增量刷新
   result.json 与 report.md。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 逐条回读 finding 对应原文，确认 quote 逐字、locator 准确、严重度与性质匹配。
2. 看 finalize 输出的 `numeric_checks` 候选（p 值与措辞/CI、多处 N/n 不一致），
   确证者提升为 finding，不确证者不写或写 `pending_review`。
3. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述；矛盾处两条证据都记录。
4. 无法确认的条目：`review_status=needs_review`，写入 `uncertainty`/`pending_human`。
5. 填 `/app/work/meta.json`：`status/overall_verdict/overall_assessment/checked_facets/
   sources/uncertainty/limitations/...`，重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 §6 自检清单；修正缺必填键、缺定位串、非逐字 quote。
2. 最终 `python -B scripts/main.py finalize`；确认 result.json 可 `json.load`、report.md
   已生成、status/verdict 诚实。
3. 时间不足时保留当前 partial 产物，在 `limitations` 写明未完成检查项，绝不补造。

时间不够时的优先级：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位+摘录 → 状态诚实。
宁缺毋滥。

## 6. 自检清单（逐条可勾选；括号内为服务的评分维度）

**A 实际效果**
- [ ] A1 result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] A2 每条 finding 六必填齐全（id/facet/locator/quote/severity/suggestion）。
- [ ] A3 finding_type 显式填写，五类（事实错误/证据不足/规范/建议/待复核）已区分。
- [ ] A4 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录。
- [ ] A5 五个检查面（研究设计/变量定义/统计方法/样本量/结果报告）覆盖或显式标注未覆盖。
- [ ] A6 六类检查面（事实核验/引用支持/方法设计/统计报告/论证链/学术规范）覆盖登记。
- [ ] A7 严重度与缺陷性质匹配：未把"没报告"写成硬伤，也未把硬伤写成建议。
- [ ] A8 方法判定区分"方法不当/可接受但报告不全/可接受可改"三档。
- [ ] A9 numeric_checks 候选已逐条复核，确证者入 findings，其余不冒充结论。

**B 使用增益**　B1 骨架先行、增量 finalize 减少返工；B2 冲突信息未隐藏，两条证据都记录；
B3 无法确认条目标 `needs_review` 并进 uncertainty/pending_human。

**C 创新增益**　C1 五类缺陷类型全用、不空置；C2 数值一致性脚本候选检查实际产出候选。

**D 可靠体验**　D1 sources 只含实际核验来源，无虚构 DOI/URL/数值；D2 空材料写
`insufficient_evidence`、缺 case.json 且无材料写 `input_error`；D3 脚本 `--help` 退出码 0、
同一输入重复运行结果一致（无随机数）；D4 缺软件/扫描版/坏编码均降级不崩溃。

**E 生态贡献**　E1 checklist/result_schema 独立成文、可被他人复用；E2 脚本纯标准库、
路径相对、无未说明依赖。

**收尾动作**：以上 A1–E2 可执行 `python -B scripts/selftest.py` 对脚本层自检（结构、--help、
合成流程、降级、确定性）；内容层（quote/locator 逐条回查、严重度匹配、六类覆盖）由 Agent
按本清单逐条勾选完成。

## 7. 降级策略

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

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源、撤回状态、
软件状态与评测分数。

## 8. 边界

一个 Skill 只解决"研究方法与统计审查"这一件事，不做通用大而全审稿。没有真实材料时输出
"材料不足"而不是示例填充。正式测试案例、白板与分数由平台提供，本 Skill 不生成、不预测。
