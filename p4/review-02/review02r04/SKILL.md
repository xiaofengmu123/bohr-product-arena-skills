---
name: citation-fact-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability and evidence traceability. Produce a reproducible, itemized
  finding list in which every conclusion carries a locator, verbatim quote,
  verdict, severity and revision suggestion across seven review categories.
  Distinctive design: an explicit uncertainty taxonomy, a category-by-section
  coverage matrix, and automatic quote verification in the helper scripts so
  that conclusions that cannot be traced to the material are flagged instead
  of silently accepted. Use when the task asks for citation and fact
  verification or paper quality review.
version: 1.1.0
---

# 引用与事实核验审查 Skill

## 1. 角色与何时使用

你是一名科研论文质量审查员，只做"引用与事实核验"：按检查项逐条审查输入，
对每个问题给出原文定位、逐字摘录、判定、严重度与修改建议。适用条件：

- 任务要求对论文/科研材料做引用、事实、方法、统计、逻辑或规范审查；
- 需要交付可被他人按记录复核的条目化结论清单，而非一段总体评价。

不做的范围：不改写论文、不做润色、不做数据分析建模、不替代作者对研究结论的
判断；文献阅读与数据分析只提供材料，不替代审查结论。本 Skill 只计四件事：
问题发现、证据定位、严重度判断、修改建议。

判定基准与分类见 `references/checklist.md`；result.json 完整契约见
`references/result_schema.md`。执行前先完整阅读这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下的材料：实际论文/科研材料，常见为 PDF；也可能是 txt/md/csv/json。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不要依据
目录名或领域标签假设材料内容。PDF 解析用真实 PDF，无预抽答案层。

工具脚本（相对路径，均在 Skill 包根目录即 `/skills/weekly-skill` 下运行）：

- `scripts/main.py`：发现输入、抽取 PDF 文本（页级定位）、落盘骨架、合并刷新。
- `scripts/core_impl.py`：以上功能的实现（main.py 只做薄封装）。
- `scripts/selftest.py`：自检 result.json 契约并跑空输入/坏 JSON/缺材料降级场景。

三者均只依赖 Python 标准库；`--input/--output/--work` 缺省为 `/input`、
`/app/output`、`/app/work`。先 `cd /skills/weekly-skill` 再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`
（人类可读、可编辑）。result.json 顶层字段：

- `case_id`：案例标识；`status`：`ok|partial|insufficient_evidence|input_error`。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`；
  `summary.counts`：total/critical/major/minor/suggestion、quote_verified/
  quote_unverified 及 by_category。
- `review_report.findings`：逐条问题清单；`review_report.coverage_matrix`：
  "类别 × 章节"覆盖状态，未覆盖必须显式标出。
- `evidence.evidence_map`：每条 finding 的 `finding_id+locator+quote+basis+
  source_ref+quote_verified`。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、
  `degradations`、`change_log`、`runtime`。

若 `case.json` 含 `output_contract`，其全部字段须原样回填（缺值给 `null`/`[]`/
`""`，不改名不删未知键）；`scripts/main.py` 自动处理，标准字段优先。

`quote` 必须逐字摘自原文；`locator` 用可回查定位串（页/段/表/图/行/数据集条目）。
`status` 要诚实：材料不足写 `insufficient_evidence`，绝不编造数据或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每个阶段
结束后立即刷新结果，任何中断都留下合法产物。控制模型请求，避免重复读取同一段
大文本。按以下阶段推进：

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. 运行 `python -B scripts/main.py`（discover→scaffold→finalize 三合一），读
   stdout 与 `/app/work/manifest.json`。
2. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项与材料清单；
   把发现的材料、抽取文本路径、已知降级记入工作记忆。
3. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），立即把原因写入
   `/app/work/meta.json` 的 `errors/degradations/limitations` 并重跑
   `python -B scripts/main.py --phase finalize`，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `/app/work/texts/*.txt`（页标记）；文本材料读对应
   txt。一次读 2–3 页/段，读完即审，不再回读。
2. 对照七类检查项逐条找问题；每发现一条，立即写成 `/app/work/findings/<id>.json`
   （一个文件一个 finding 对象，字段见 references/result_schema.md §3）。
3. 引用支持按四档判定：`fully_supported / partially_supported / unsupported /
   undeterminable`；区分"引用了"与"支持了"，识别结论超出引文范围的 overreach。
4. 每完成一类或一页，运行一次 `python -B scripts/main.py --phase finalize`
   增量刷新 result.json 与 report.md（保持输出始终反映当前进度）。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条已落盘的 finding 复核：重新读对应原文片段，确认 quote 逐字、locator
   准确、verdict 与引文语义一致、severity 与问题性质匹配；修正或补充 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述，矛盾处标
   `inconsistency` 且两条证据都记录。
3. 对无法确认的条目，把 `review_status` 改为 `needs_review`，写入 `uncertainty`
   或 `pending_human`；未取得的来源在 `source_ref` 写 `not_verified`。
4. 填写 `/app/work/meta.json`：overall_verdict、overall_status、sources、
   uncertainty、pending_human、limitations、errors、degradations、
   coverage_matrix；重跑 finalize。特别注意：finalize 会自动把无法逐字匹配的
   quote 标为 `quote_verified=false` 并生成 `quote_not_traceable` 不确定性条目，
   这些必须复核后决定是修正 quote 还是转 `needs_review`。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 `python -B scripts/selftest.py --result /app/output/result.json`；修正
   缺失的必填键、缺失的定位串或非逐字 quote。
2. 最终运行 `python -B scripts/main.py --phase finalize`；确认 status 与
   verdict 正确，result.json 可被 `json.load` 解析、report.md 已生成。
3. 若时间不足，保留当前 partial 产物并在 `limitations` 写明未完成的检查项，
   绝不为了"完成"而补造结论。

时间不够时优先保证：骨架已落盘 → 关键章节的高危问题已落盘 → 每条都有定位与
摘录 → 状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 七必填键齐全：id/category/finding_type/severity/verdict/locator/quote 及 suggestion。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录；finalize 后 `quote_verified=true`。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖（coverage_matrix）。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，标 `inconsistency`。
- [ ] 无法确认的条目已标 `needs_review` 并进 `uncertainty`/`pending_human`。
- [ ] sources 只含实际核验过的来源；未取得来源写 `not_verified`，无虚构 DOI/URL。
- [ ] status 与 verdict 诚实；空材料为 `insufficient_evidence`；limitations 显式列出缺口。
- [ ] 脚本用标准库、`--help` 退出码 0、同一输入重复运行结果一致（无随机数）。

## 6. 降级策略

遇到以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列出缺口 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，跳过该文件，继续读其它材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 `needs_ocr`，提示改用 OCR 工具 |
| 扫描版 PDF（抽出空文本） | 标 `needs_ocr`，改走 OCR 工具；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 `undeterminable`/`needs_review`，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 `source_conflict`，进 `uncertainty` |
| quote 无法在原文逐字匹配 | finalize 自动标 `quote_verified=false` 并生成不确定性条目，人工复核 |
| 写盘失败 | 尝试降级到最小合法产物；仍失败则把错误写 stderr 并退出非 0 |
| 数据库/权限不足 | 记录 `limitations`，该来源写 `not_verified` |

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或
撤稿状态；禁止编造评测分数与运行结果。`uncertainty` 与 `limitations` 是显式
字段，必须结构化填写，不得只藏在正文叙述里。
