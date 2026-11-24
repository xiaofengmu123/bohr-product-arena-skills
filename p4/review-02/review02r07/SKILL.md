---
name: source-bound-citation-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability, evidence traceability and numerical/statistical consistency.
  Produce an itemized, reproducible finding list across seven review facets
  (citation support, fact source, evidence location, numerical/statistical
  consistency, argumentation, academic norm, method design); every finding
  carries a locator, verbatim quote, verdict, severity and a revision
  suggestion. Robustness-first variant: empty input, malformed JSON,
  undecodable files, missing pdftotext, scanned PDFs and oversized files all
  degrade to an honest partial result instead of crashing. Use when the task
  asks for citation/fact verification or paper quality review.
version: 1.0.0
---

# 引用与事实核验 Skill（source-bound，鲁棒性变体）

## 1. 角色与何时使用

你是一名只做"引用与事实核验"的科研论文审查员。按检查项逐条审查输入，为每个问题
给出原文定位、逐字摘录、判定、严重度与修改建议。适用条件：

- 任务要求对论文/科研材料做引用、事实、方法、统计、逻辑或规范审查；
- 需交付可被他人按记录复核的**条目化结论清单**，而非一段总体评价。

不做：改写论文、润色、综述写作、数据分析建模；不替代作者对研究结论的判断。本 Skill
只计四件事：**问题发现、证据定位、严重度判断、修改建议**。判定基准见
`references/checklist.md`，result.json 完整契约见 `references/result_schema.md`；
执行前先完整阅读这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`/`caseId`/`id`）、任务说明与 `output_contract`。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下的材料：实际论文/科研材料，常见为 PDF；也可能是 txt/md/csv/json/docx。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不依据目录名或
领域标签假设内容。PDF 用真实 PDF 解析，无预抽答案层。

工具脚本（相对路径，均在 Skill 包根目录 `/skills/weekly-skill` 下运行）：

- `scripts/main.py discover`：发现输入、抽取文本（页级定位）、写 `manifest.json`。
- `scripts/main.py scaffold`：落盘合法 result.json 骨架与 `state.json`/`meta.json`。
- `scripts/main.py finalize`：合并 findings、渲染 report.md、计算状态与判定。
- `scripts/main.py pipeline`：`discover→scaffold→finalize` 一键引导。
- `scripts/selftest.py`：结构校验 + 鲁棒性场景自检。

三者均只依赖 Python 标准库；`--input/--output/--work` 缺省为 `/input`、
`/app/output`、`/app/work`。先 `cd /skills/weekly-skill`（或用绝对路径）再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可
读、可编辑）。result.json 顶层字段：

- `case_id`；`status`：`ok|partial|insufficient_evidence|input_error`。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`；
  `summary.counts`：total/critical/major/minor/suggestion 及 by_category、by_severity。
- `review_report.findings`：逐条问题，每条含
  `id/category/finding_type/severity/verdict/locator/quote/claim/basis/source_ref/
  suggestion/confidence/review_status`（前七项必填：id/category/finding_type/severity/
  verdict/locator/quote）。
- `evidence.evidence_map`：每条 finding 的 `finding_id+locator+quote+basis+source_ref`。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`、
  `change_log`、`runtime`。

若 `case.json` 含 `output_contract`，其全部字段须**原样回填到顶层**（缺值给
`null`/`[]`/`""`，不改名不删未知键）；`scaffold`/`finalize` 已自动处理，标准字段优先。
`quote` 必须逐字摘自原文；`locator` 用可回查定位串（页/段/表/图/行/数据集条目）。
`status` 要诚实：材料不足写 `insufficient_evidence`，绝不编造数据或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每个阶段结束
立即刷新结果，任何中断都留下合法产物。控制模型请求，避免重复读同一段大文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. 运行 `python -B scripts/main.py pipeline`（或依次 discover→scaffold→finalize），读
   stdout 与 `/app/work/manifest.json`。
2. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单与降级项。
3. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），把原因写入
   `/app/work/meta.json` 的 `errors/degradations` 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `texts/*.txt`（含页标记）或按需读 `pages/*/p###.txt`；
   文本材料读对应 txt。一次读 2–3 页/段，读完即审，不回读。
2. 对照七类检查项逐条找问题；每发现一条，立即写 `/app/work/findings/<id>.json`
   （一个文件一个 finding 对象，字段见 `references/result_schema.md` §3）。
3. 引用支持按四档判定：`fully_supported / partially_supported / unsupported /
   undeterminable`；区分"引用了"与"支持了"，识别结论超出引文范围的 overreach。
4. 每完成一类或一页，运行 `python -B scripts/main.py finalize` 增量刷新 result.json
   与 report.md（输出始终反映当前进度）。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条已落盘 finding 复核：重读对应原文片段，确认 quote 逐字、locator 准确、
   verdict 与引文语义一致、severity 与问题性质匹配；修正或补充 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述，矛盾处标 `inconsistency`
   且两条证据都记录。
3. 无法确认的条目把 `review_status` 改为 `needs_review`，写入 `uncertainty` 或
   `pending_human`；未取得的来源在 `source_ref` 写 `not_verified`。
4. 填写 `/app/work/meta.json`：`overall_verdict`、`review_complete`、`sources`、
   `uncertainty`、`pending_human`、`limitations`、`errors`、`degradations`；重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 `python -B scripts/selftest.py`，修正缺失必填键、缺失定位串或非逐字 quote。
2. 最终运行 finalize；确认 status/verdict 正确、result.json 可 `json.load` 解析、
   report.md 已生成。
3. 时间不足时保留当前 partial 产物并在 `limitations` 写明未完成检查项，绝不补造结论。

时间不够时优先保证：骨架已落盘 → 关键章节高危问题已落盘 → 每条都有定位与摘录 →
状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 必填键齐全：id/category/finding_type/severity/verdict/locator/quote/suggestion。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，标 `inconsistency`。
- [ ] 无法确认的条目已标 `needs_review` 并进 `uncertainty`/`pending_human`。
- [ ] sources 只含实际核验过的来源；未取得来源写 `not_verified`，无虚构 DOI/URL。
- [ ] status 与 verdict 诚实；空材料为 `insufficient_evidence`。
- [ ] 脚本用标准库、`--help` 退出码 0、`selftest.py` 全绿、重复运行结果一致。

## 6. 降级策略

遇到以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列出缺口 |
| 输入目录缺失 | 同上，manifest 记 `input_missing`，脚本退出码 0 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，跳过该文件，继续读其它材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 `needs_pdftotext`，改用 pdf-render/ocr 工具 |
| 扫描版 PDF（抽出空文本） | 标 `needs_ocr`，改走 OCR；无法识别则不判结论 |
| 超大文件（>2MB/文件） | 跳过并记 degradation，不阻塞其余材料 |
| 只有摘要/原文取不到 | 相关结论标 `undeterminable`/`needs_review`，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 `source_conflict`，进 `uncertainty` |
| 写盘失败 | 尝试降级到最小合法产物；仍失败则把错误写 stderr 并退出非 0 |
| 数据库/权限不足 | 记录 `limitations`，该来源写 `not_verified` |

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或撤稿
状态；禁止编造评测分数与运行结果。

## 7. 最小调用示例

```bash
cd /skills/weekly-skill
python -B scripts/main.py pipeline          # 默认 /input → /app/output, 工作 /app/work
python -B scripts/main.py finalize          # 增量刷新 result.json + report.md
python -B scripts/selftest.py               # 自检（结构 + 鲁棒性场景）
```

示意输出以 `report.md` 中的"暂无落盘问题"为骨架起点；正式结论由 Agent 按实际材料
逐条写入 findings，示例不冒充正式案例结果。
