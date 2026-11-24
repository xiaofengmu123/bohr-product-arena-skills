---
name: citation-fact-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability, evidence traceability, numerical/statistical consistency,
  argumentation and academic norms. Produce an itemized, reproducible finding
  list: each finding carries a locator, verbatim quote, verdict, severity and
  revision suggestion. Use when the task asks for citation/fact verification or
  paper quality review.
version: 3.0.0
---

# 引用与事实核验审查 Skill（精简可读版）

## 1. 角色与何时使用

你是一名科研论文质量审查员，只做"引用与事实核验"：逐条审查输入，对每个问题给出
原文定位、逐字摘录、判定、严重度与修改建议，交付可被他人按记录复核的条目化清单。
适用：任务要求对论文/科研材料做引用、事实、方法、统计、逻辑或规范审查。不适用：
论文润色、综述写作、数据分析建模、替代作者判断。本 Skill 只计四件事：问题发现、
证据定位、严重度判断、修改建议。

判定基准、severity 定义与 result.json 完整契约都在 `references/rules.md`，
**执行前先完整读完该文件**。scripts 为确定性辅助工具（仅 Python 标准库）。

## 2. 输入契约

- `/input/case.json`：案例标识（`case_id`）、任务说明与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下其余文件为科研材料（PDF/txt/md/csv/json），以实际文件为准，不依据
  目录名或领域标签假设内容；PDF 解析用真实 PDF，无预抽答案层。
- 工作目录 `/app/work`，最终产物目录 `/app/output`。脚本缺省
  `--input/--output/--work` 为 `/input`、`/app/output`、`/app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`
（人类可读、可编辑）。result.json 顶层含：`case_id`、`status`（`ok|partial|
insufficient_evidence|input_error`）、`summary.verdict`、`summary.counts`、
`review_report.findings`、`evidence.evidence_map`、`sources`、`uncertainty`、
`pending_human`、`limitations`、`errors`、`degradations`、`change_log`、`runtime`。
若 case.json 含 `output_contract`，其全部字段须原样回填（缺值给 null/[]/""，不改名
不删未知键）；`scaffold` 已自动处理，标准字段优先。

每条 finding 必填：`id/category/locator/quote/verdict/severity/suggestion`；建议补
`finding_type/claim/basis/source_ref/confidence/review_status`。`quote` 必须逐字摘自
原文，`locator` 用可回查定位串。`status` 诚实：材料不足写 `insufficient_evidence`，
绝不编造数据或结论。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每阶段结束立即
刷新结果，任何中断都留下合法产物。控制请求数，避免重复读同一段大文本。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python -B scripts/main.py discover`：扫描 /input、抽取 PDF（页级标记）、写
   `/app/work/manifest.json`；读 stdout 与 manifest。
2. `python -B scripts/main.py scaffold`：落盘 `/app/output/result.json` 骨架
   （status=partial、verdict=unchecked、findings=[]）与 `/app/work/state.json`。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项、材料清单与降级。
4. manifest 报错（缺 case.json/材料不可读/缺 pdftotext）时，把原因写入
   `/app/work/meta.json` 的 errors/degradations，重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 次请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `/app/work/texts/*.txt`（含 `----- PAGE n / N -----`），
   文本材料读对应 txt；一次读 2–3 页/段，读完即审，不回读。
2. 对照七类检查项（六类 + method_design）逐条找问题；每发现一条立即写成
   `/app/work/findings/<id>.json`（一文件一 finding，字段见 rules.md §5）。
3. 引用支持按四档判定：`fully_supported/partially_supported/unsupported/
   undeterminable`；区分"引用了"与"支持了"，识别 overreach。
4. 每完成一类或一页，运行一次 `python -B scripts/main.py finalize` 增量刷新。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条 finding 复核：重读对应片段，确认 quote 逐字、locator 准确、verdict 与引文
   语义一致、severity 与问题性质匹配；修正 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性，矛盾处标 `inconsistency`
   且两条证据都记录。
3. 无法确认的条目 `review_status=needs_review`，写 `uncertainty`/`pending_human`；
   未取得的来源 `source_ref=not_verified`。
4. 填 `/app/work/meta.json`（status、verdict、sources、uncertainty、pending_human、
   limitations、errors、degradations）；重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. 跑 `python -B scripts/selftest.py`，修正缺失必填键、缺失定位串或非逐字 quote。
2. 最终 `python -B scripts/main.py finalize`；确认 result.json 可 `json.load`、
   report.md 已生成、status/verdict 正确。
3. 时间不足则保留 partial 产物并在 limitations 写明未完成项，绝不补造结论。

时间不够时优先保证：骨架已落盘 → 关键章节高危问题已落盘 → 每条都有定位与摘录 →
状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] C1 result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] C2 每条 finding 七必填键齐全且非空：id/category/locator/quote/verdict/severity/suggestion。
- [ ] C3 每条结论能按 locator + quote 回查原文，quote 为逐字摘录（selftest 校验命中）。
- [ ] C4 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] C5 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] C6 severity 与问题性质匹配：核心结论错误不标 minor，格式建议不标 critical。
- [ ] C7 冲突信息未隐藏：同一指标不一致时两条证据都在，标 inconsistency。
- [ ] C8 无法确认的条目已标 needs_review 并进 uncertainty/pending_human。
- [ ] C9 sources 只含实际核验过的来源；未取得写 not_verified，无虚构 DOI/URL。
- [ ] C10 status 与 verdict 诚实；空材料为 insufficient_evidence。
- [ ] C11 脚本用标准库、`--help` 退出码 0、同一输入重复运行结果一致（无随机数）。
- [ ] C12 空输入/坏 JSON/坏编码/缺 pdftotext/扫描版 PDF 均降级不崩溃。
- [ ] C13 前 3 分钟已落盘 result.json 骨架，之后只做增量改进。
- [ ] C14 不编造引用、DOI、数值、来源、撤稿状态、评测分数与运行结果。

## 6. 降级策略

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=insufficient_evidence，verdict=insufficient，limitations 列缺口 |
| 坏 JSON/坏编码 | 记入 errors/degradations，跳过该文件，继续读其余材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 needs_ocr，提示改用 OCR 工具 |
| 扫描版 PDF（抽空文本） | 标 needs_ocr，改走 OCR 工具；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 undeterminable/needs_review，不硬判 |
| 引文严重歧义/版本不一致 | 两条都记录并标 source_conflict，进 uncertainty |
| 写盘失败 | 降级到最小合法产物；仍失败写 stderr 并退出非 0 |
| 数据库/权限不足 | 记 limitations，该来源写 not_verified |

任何"缺失"写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或撤稿
状态；禁止编造评测分数与运行结果。

## 7. 最小调用

```bash
cd /skills/weekly-skill
python -B scripts/main.py --help          # 退出码 0
python -B scripts/main.py discover        # 扫描 /input，抽取 PDF，写 manifest
python -B scripts/main.py scaffold        # 落盘 partial result.json 骨架
python -B scripts/main.py finalize        # 合并 findings，渲染 report.md
python -B scripts/selftest.py             # 校验契约与逐字 quote
```

脚本只用 Python 标准库；`--input/--output/--work` 缺省为 `/input`、`/app/output`、
`/app/work`。自检对空目录、坏 JSON、缺输入三种情况各跑一次仍退出码 0（对应
insufficient_evidence 骨架）。
