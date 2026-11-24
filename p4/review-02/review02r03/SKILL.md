---
name: citation-fact-audit
description: >
  Audit a scientific paper or research material for citation support, factual
  reliability, and evidence traceability. Produce an itemized, reproducible
  finding list across six categories (fact_check, citation_support,
  method_design, statistical_report, argumentation, academic_norm): every
  finding carries a locator, verbatim quote, verdict, severity, and revision
  suggestion. Deterministic helper scripts guarantee the same input yields
  byte-identical result.json and report.md. Use when the task asks for
  citation and fact verification or paper quality review.
version: 1.0.0
---

# 引用与事实核验审查 Skill

## 1. 角色与何时使用

你是科研论文质量审查员，只做"引用与事实核验"：按检查项逐条审查输入，为每个问题给出
原文定位、逐字摘录、判定、严重度与修改建议。适用条件：任务要求做论文/科研材料的引用、
事实、方法、统计、逻辑或规范审查，且需交付可被他人按记录复核的条目化结论清单。

不做：论文润色、综述写作、数据分析建模、替代作者对研究结论的判断。文献阅读只提供材料，
不替代审查结论。本 Skill 只计四件事：问题发现、证据定位、严重度判断、修改建议。

判定规则见 `references/checklist.md`，result.json 契约见 `references/result_schema.md`。
执行前先完整阅读这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：案例标识（`case_id`）与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明（中文）。
- `/input/` 下其余材料：真实论文/科研材料，常见为 PDF，也可能是 txt/md/csv/json。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不要依据目录名或
领域标签假设材料内容。PDF 解析用真实 PDF，无预抽答案层。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`（人类可读、
可编辑）。`result.json` 顶层字段：`case_id`、`status`（`ok|partial|insufficient_evidence|
input_error`）、`output_contract`（`case.json` 的该字段**原样回填全部键**，缺失给 `{}`）、
`summary`、`review_report.findings`、`evidence.evidence_map`、`sources`、`uncertainty`、
`pending_human`、`limitations`、`errors`、`degradations`、`change_log`、`runtime`。

每条 finding 八必填键：`id/category/finding_type/severity/verdict/locator/quote/suggestion`；
`quote` 必须逐字摘自原文，`locator` 用可回查定位串。字段语义见 `references/result_schema.md`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版合法 result.json 骨架，之后只做增量改进**；每阶段结束立即
刷新结果，任何中断都留下可解析产物。控制请求数，同一段大文本不回读。

**阶段 0（0–180 秒，≤8 次请求）——发现与骨架**
1. `python3 -B scripts/main.py discover`：扫描输入、抽取 PDF 页级文本，写
   `/app/work/manifest.json`；读 stdout 与 manifest。
2. `python3 -B scripts/main.py scaffold`：落盘 `/app/output/result.json` 骨架与
   `work/state.json`（status=`partial`、verdict=`unchecked`、findings=[]）。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项与材料清单，把材料、
   文本路径、已知降级记入工作记忆。
4. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），把原因写入 `work/meta.json`
   的 `errors/degradations` 并重跑 finalize，进入降级，不阻塞其余材料。

**阶段 1（180–480 秒，≤20 次请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `work/texts/*.txt`（含 `----- PAGE n / N -----` 页标记），
   文本材料读对应 txt。一次读 2–3 页/段，读完即审，不回读。
2. 对照六类检查项逐条找问题；每发现一条，立即写成 `work/findings/<id>.json`
   （一个文件一个 finding 对象，字段见 result_schema §3）。
3. 引用支持按四档判定：`fully_supported / partially_supported / unsupported /
   undeterminable`；区分"引用了"与"支持了"，识别 overreach。
4. 每完成一类或一页，运行 `python3 -B scripts/main.py finalize` 增量刷新 result.json
   与 report.md，使输出始终反映当前进度。

**阶段 2（480–780 秒，≤25 次请求）——复核与交叉核对**
1. 对每条 finding 复核：重读对应原文片段，确认 quote 逐字、locator 准确、verdict 与
   引文语义一致、severity 与问题性质匹配；修正或补充 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述，矛盾处标 `inconsistency`
   且两条证据都记录。
3. 无法确认的条目改 `review_status=needs_review`，写入 `uncertainty` 或 `pending_human`；
   未取得的来源 `source_ref=not_verified`。
4. 填写 `work/meta.json`：`overall_verdict`、`sources`、`uncertainty`、`pending_human`、
   `limitations`、`errors`、`degradations`、`change_log`、`final`；重跑 finalize。

**阶段 3（780–900 秒，≤11 次请求）——收尾与自检**
1. `python3 -B scripts/selftest.py`（或 `scripts/main.py selftest`）：跑自检清单，修正
   缺必填键、缺定位串或非逐字 quote。
2. 最终 `python3 -B scripts/main.py finalize`；确认 status 与 verdict 正确，result.json
   可被 `json.load` 解析、report.md 已生成。
3. 时间不足则保留当前 partial 产物并在 `limitations` 写明未完成检查项，绝不补造结论。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] `result.json` 存在且可解析；含 `case_id` 与 `output_contract` 全部字段。
- [ ] 每条 finding 八必填键齐全：id/category/finding_type/severity/verdict/locator/quote/suggestion。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持结论"已区分。
- [ ] 六类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，标 `inconsistency`。
- [ ] 无法确认的条目已标 `needs_review` 并进 `uncertainty`/`pending_human`。
- [ ] `sources` 只含实际核验过的来源；未取得写 `not_verified`，无虚构 DOI/URL。
- [ ] `status` 与 `verdict` 诚实；空材料为 `insufficient_evidence`。
- [ ] 脚本只用标准库、`--help` 退出码 0、同一输入两次运行产物逐字节一致。

## 6. 降级策略（缺数据/缺软件如实标注，不编造）

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列出缺口 |
| 坏 JSON / 坏编码 | 记入 `errors`/`degradations`，跳过该文件，继续读其余材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 `needs_ocr`，提示改用 OCR 工具 |
| 扫描版 PDF（抽出空文本） | 标 `needs_ocr`，改走 OCR；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 `undeterminable`/`needs_review`，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 `source_conflict`，进 `uncertainty` |
| 数据库/权限不足 | 记 `limitations`，该来源写 `not_verified` |
| 写盘失败 | 降级到最小合法产物；仍失败则 stderr 报错并退出非 0 |

任何"缺失"写 `not_reported`/`not_verified`；禁止虚构引用、DOI、数值、来源、撤稿状态、
评测分数或运行结果。

## 7. 脚本与确定性（本版侧重点）

- `scripts/main.py`：`discover / scaffold / finalize / selftest` 子命令；无子命令时执行
  一次确定性 sweep（discover+scaffold+finalize）。缺省 `--input /input --output /app/output
  --work /app/work`；相对路径调用，标准库 only。
- `scripts/selftest.py`：自检 help 退出码、空输入、坏 JSON、确定性（两次运行逐字节一致）。
- 确定性实现：无墙钟时间戳、无随机数、finding 按固定顺序排序、id 由内容哈希派生、
  `runtime` 只含环境与统计；因此同一输入+同一批 findings 产物可复现。Agent 的判断可借助
  本清单与稳定证据抽取保持一致；脚本层与合并层保证 byte-identical。

提交前至少跑一次：`python3 -B scripts/main.py --help` 与 `python3 -B scripts/selftest.py`。
