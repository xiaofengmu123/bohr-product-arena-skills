---
name: evidence-led-citation-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability and evidence traceability. Produce an itemized, reproducible
  finding list where every conclusion points to a deduplicated evidence entry
  (locator + verbatim quote) and is compiled through a machine-readable
  intermediate model (plan.json / evidence/*.json / audit_model.json). Use when
  the task asks for citation and fact verification or paper quality review.
version: 2.0.0
---

# 引用与事实核验审查 Skill（证据先行 / 可复核中间表示）

## 1. 角色与何时使用

你是一名科研论文质量审查员，只做"引用与事实核验"：按检查项逐条审查输入，对每个
问题给出原文定位、逐字摘录、判定、严重度与修改建议。适用条件：

- 任务要求做论文/科研材料的引用、事实、方法、统计、逻辑或规范审查；
- 需要交付可被他人按记录复核的条目化结论清单（而非一段总体评价）。

不做：不改写论文、不做润色、不做数据分析建模、不替代作者对研究结论的判断；文献
阅读与数据分析只提供材料，不替代审查结论。本 Skill 只计四件事：问题发现、证据
定位、严重度判断、修改建议。

判定基准见 `references/checklist.md`；输出与中间产物契约见
`references/result_schema.md`。执行前先完整阅读这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下的材料：实际论文/科研材料，常见为 PDF；也可能是 txt/md/csv/json。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不依据目录名或
领域标签假设材料内容。PDF 解析用真实 PDF，无预抽答案层。

工具脚本（相对路径，均在 Skill 包根运行，即 `/skills/weekly-skill`）：

- `scripts/main.py`：薄入口，分派 `discover/scaffold/finalize/selftest` 子命令。
- `scripts/selftest.py`：独立自检入口（等价于 `main.py selftest`）。

三者只依赖 Python 标准库；`--input/--output/--work` 缺省为 `/input`、
`/app/output`、`/app/work`。先 `cd /skills/weekly-skill`（或用绝对路径）再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`
（人类可读、可编辑）。result.json 顶层字段：

- `case_id`；`status`：`ok|partial|insufficient_evidence|input_error`。
- `summary.verdict`：`pass|issues_found|needs_review|unchecked|insufficient`；
  `summary.counts`：total/critical/major/minor/suggestion 及 by_category。
- `review_report.findings`：逐条问题，每条含 `id/category/finding_type/severity/
  verdict/locator/quote/claim/basis/source_ref/suggestion/confidence/
  review_status/evidence_ids`（必填前七项）。
- `evidence.ledger` 与 `evidence.evidence_map`：每条 finding 的
  `finding_id+locator+quote+basis+source_ref+evidence_ids`。
- `sources`、`uncertainty`、`pending_human`、`limitations`、`errors`、
  `degradations`、`change_log`、`runtime`、`audit_model_ref`。

若 `case.json` 含 `output_contract`，其全部字段须原样回填到 result.json 顶层
（缺值给 `null`/`[]`/`""`，不改名不删未知键）；与标准字段同名时标准字段优先。
`quote` 必须逐字摘自原文；`locator` 用可回查定位串（页/段/表/图/行/数据集条目）。
`status` 要诚实：材料不足写 `insufficient_evidence`，绝不编造数据或结论。

## 4. 中间表示与可复核（本版设计取舍）

核心升级点：**证据先于结论，结论从可复核的中间模型渲染**。Agent 不直接改
`result.json`，而是依次落盘：

1. `plan.json`（scaffold 生成）——审查计划：facets、材料清单、阅读队列。
2. `evidence/<EV-id>.json`——原始证据条目，只含 `locator + 逐字 quote +
   source_ref`，不下结论；同一原文片段只抽取一次，供多个 finding 复用。
3. `findings/<F-id>.json`——审查结论，用 `evidence_ids` 引用证据，写判定与建议。
4. `meta.json`——overall_verdict、status、sources、uncertainty、pending_human、
   limitations、errors、degradations。
5. `finalize` 汇总成 `audit_model.json`（单一中间事实源），再确定性渲染
   `result.json` 与 `report.md`。

收益：每条结论都能按 `evidence_ids → locator + quote` 由第二人重建；证据去重后
可交叉复用；`audit_model.json` 让"逐条回查"与"复现同一批结论"落到一个可解析文件上。

## 5. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每个阶段结束
后立即 `finalize` 刷新结果，任何中断都留下合法产物。控制请求数，不回读同一段大文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py scaffold`，落盘 `/app/output/result.json` 骨架
   （`status=partial`、`verdict=unchecked`、`findings=[]`）、`plan.json`、
   `meta.json`、`state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、facets 与材料清单；
   把发现的材料、抽取文本路径、已知降级记入工作记忆。
4. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），立即把原因写入
   `meta.json` 的 `errors/degradations` 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——逐段抽取与逐条判定**
1. 按 `plan.json.reading_queue` 逐段读原文：PDF 读 `texts/*.txt`（含 `----- PAGE n / N -----`
   页标记）；文本材料读对应 txt。一次读 2–3 页/段，读完即审，不回读。
2. 先写 `evidence/<EV-id>.json`：候选断言/引用/数值/方法陈述的 `locator+quote`。
3. 再写 `findings/<F-id>.json`：对照六类检查项给出判定与 `evidence_ids`。
4. 引用支持按四档判定：`fully_supported / partially_supported / unsupported /
   undeterminable`；区分"引用了"与"支持了"，识别 overreach。
5. 每完成一类或一页，运行一次 `python -B scripts/main.py finalize` 增量刷新
   result.json 与 report.md。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条 finding 复核：重读对应原文片段，确认 quote 逐字、locator 准确、verdict
   与引文语义一致、severity 匹配；修正或补充 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述；矛盾处标
   `inconsistency` 且两条证据都记录。
3. 无法确认的条目把 `review_status` 改 `needs_review`，写入 `uncertainty` 或
   `pending_human`；未取得的来源在 `source_ref` 写 `not_verified`。
4. 填写 `meta.json` 的 overall_verdict、sources、uncertainty、pending_human、
   limitations、errors、degradations；重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 `python -B scripts/main.py selftest`；修正缺失的必填键、缺失定位串或
   非逐字 quote。
2. 最终运行 `python -B scripts/main.py finalize`；确认 status 与 verdict 正确，
   result.json 可被 `json.load` 解析、report.md 已生成。
3. 时间不足则保留当前 partial 产物并在 `limitations` 写明未完成项，绝不补造结论。

时间不够时优先保证：骨架已落盘 → 关键章节高危问题已落盘 → 每条都有定位与摘录 →
状态诚实。宁缺毋滥。

## 6. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 七必填键齐全：id/category/locator/quote/verdict/severity/suggestion。
- [ ] 每条结论能按 `evidence_ids → locator + quote` 回查原文，quote 为逐字摘录。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] 六类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，标 `inconsistency`。
- [ ] 无法确认的条目已标 `needs_review` 并进 `uncertainty`/`pending_human`。
- [ ] sources 只含实际核验过的来源；未取得来源写 `not_verified`，无虚构 DOI/URL。
- [ ] status 与 verdict 诚实；空材料为 `insufficient_evidence`。
- [ ] 脚本用标准库、`--help` 退出码 0、同一输入重复 finalize 不改变结论。
- [ ] `audit_model.json` 已生成且能重建全部结论与证据链（中间表示完整）。

## 7. 降级策略

遇到以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列出缺口 |
| 坏 JSON / 坏编码 | 记录到 `errors`/`degradations`，跳过该文件，继续读其它材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 `needs_ocr`，提示改用 OCR 工具 |
| 扫描版 PDF（抽出空文本） | 标 `needs_ocr`，改走 OCR 工具；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 `undeterminable`/`needs_review`，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 `source_conflict`，进 `uncertainty` |
| 写盘失败 | 尝试降级到最小合法产物；仍失败则把错误写 stderr 并退出非 0 |
| 数据库/权限不足 | 记录 `limitations`，该来源写 `not_verified` |

任何"缺失"都写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或
撤稿状态；禁止编造评测分数与运行结果。

## 8. 最小调用示例（示意，非正式案例）

```bash
cd /skills/weekly-skill
python -B scripts/main.py discover
python -B scripts/main.py scaffold
# （Agent 读原文，写 evidence/<EV-id>.json 与 findings/<F-id>.json）
python -B scripts/main.py finalize
python -B scripts/main.py selftest
```

示意输出（标明为示例）：`finalize` 打印 `case_id / status / verdict / evidence 数 /
findings 数 / problems 数` 与 counts；`selftest` 逐项打印 `[PASS]/[FAIL]` 并返回
0/1。正式结果以平台固定案例的 `/app/output/result.json` 为准。
