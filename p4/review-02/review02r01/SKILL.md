---
name: citation-fact-audit-trace
description: >
  Audit scientific papers and research materials for citation support, source
  reliability, evidence traceability, numerical/statistical consistency,
  argument structure, and academic norms. Produce an itemized, reproducible
  finding list where every conclusion carries a structured locator and a
  verbatim quote, backed by a granular evidence map (role, character span,
  deterministic sha256 quote hash, source status) plus a two-sided conflict
  ledger. Use when the task asks for citation/fact verification or paper
  quality review. Requires only the Python standard library.
version: 1.1.0
---

# 引用与事实核验审查 Skill（证据溯源强化版）

## 1. 角色与何时使用

你是一名科研论文质量审查员，只做"引用与事实核验"：按检查项逐条审查输入，
对每个问题给出原文定位、逐字摘录、判定、严重度与修改建议，且每条结论都能
按 `locator + quote` 回查原文。适用条件：

- 任务要求做论文/科研材料的引用、事实、方法、统计、逻辑或规范审查；
- 需要交付条目化、可被他人按记录复核的结论清单（而非一段总体评价）。

不做：改写论文、润色、数据分析建模、替代作者对研究结论的判断。文献阅读与
数据分析只提供材料，不替代审查结论。本 Skill 只计四件事：问题发现、证据
定位、严重度判断、修改建议。判定基准见 `references/audit-rules.md`，
result.json 完整契约见 `references/result-schema.md`；执行前先读完这两个文件。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json`：`case_id`、任务说明与 `output_contract`（若有）。
- `/input/task.md`：任务与检查项说明。
- `/input/` 下材料：实际论文/科研材料，常见为 PDF，也可能 txt/md/csv/json。

工作目录 `/app/work`，最终产物目录 `/app/output`。以实际文件为准，不依据
目录名或领域标签假设内容；PDF 用真实 PDF 解析，无预抽答案层。

脚本（均只用标准库，相对路径，包根 `/skills/weekly-skill` 下运行）：

- `scripts/main.py discover`：发现输入、抽取 PDF 文本（页级定位）、写 manifest。
- `scripts/main.py scaffold`：立即落盘合法 result.json 骨架 + state/meta。
- `scripts/main.py finalize`：合并 findings、构建证据映射、渲染 report.md。
- `scripts/main.py integrity`：重算 quote 哈希并回放 quote 验证逐字存在。
- `scripts/main.py selftest` / `scripts/selftest.py`：包级自检。

`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。

## 3. 输出契约

必须产出 `/app/output/result.json`（机器可复核）与 `/app/output/report.md`
（人类可读、可编辑）。result.json 顶层字段：`case_id`、`status`、`summary`、
`review_report.findings`、`evidence`、`sources`、`uncertainty`、`pending_human`、
`limitations`、`errors`、`degradations`、`change_log`、`runtime`。

若 `case.json` 含 `output_contract`，其全部字段原样回填（缺值给 `null`/`[]`/
`""`，不改名不删未知键）；`scaffold.py` 已自动处理，标准字段优先。`status`
取 `ok|partial|insufficient_evidence|input_error`；`summary.verdict` 取
`pass|issues_found|needs_review|unchecked|insufficient`。

**证据溯源（本版核心）**：每条 finding 必须带结构化 `locator`（page/para/
section/table/figure/line/raw）与逐字 `quote`；`finalize` 为每条 finding 生成
至少一条 `primary_support` 证据，并把它与 `evidence` 数组中的反面/上下文证据
一起写入 `evidence.evidence_map`——每条证据含 `evidence_id/role/quote_span/
quote_hash/basis/source_ref/source_status/review_status`。冲突指标双侧保留并
写入 `evidence.conflicts`。`quote` 必须逐字摘自原文，`status` 要诚实。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

原则：**前 3 分钟内先落盘一版 result.json 骨架，之后只做增量改进**；每个阶段
结束立即刷新结果，任何中断都留下合法产物。控制请求数，避免重复读同一段大文本。

**阶段 0（0–180 秒，≤8 请求）——发现与骨架**
1. `python -B scripts/main.py discover`，读 stdout 与 `/app/work/manifest.json`。
2. `python -B scripts/main.py scaffold`，落盘 result.json 骨架（status=`partial`、
   verdict=`unchecked`、findings=[]、evidence_map=[]）与 state/meta。
3. 读 `/input/case.json` 与 `/input/task.md`，确认 case_id、检查项与材料清单；
   把发现的材料、抽取文本路径、已知降级记入工作记忆。
4. 若 manifest 报错（缺 case.json/材料不可读/缺 pdftotext），把原因写入
   `/app/work/meta.json` 的 errors/degradations 并重跑 finalize，进入降级。

**阶段 1（180–480 秒，≤20 请求）——逐段审查与逐条落盘**
1. 按材料逐段读原文：PDF 读 `/app/work/texts/*.txt`（页标记）；文本材料读对应
   txt。一次读 2–3 页/段，读完即审，不再回读。
2. 对照七类检查项逐条找问题；每发现一条，立即写 `/app/work/findings/<id>.json`
   （一个文件一个 finding 对象，字段见 result-schema.md §3；id 零填充如 F0001）。
3. 引用支持按四档判定：`fully_supported / partially_supported / unsupported /
   undeterminable`；区分"引用了"与"支持了"，识别 overreach。
4. 每完成一类或一页，运行一次 `python -B scripts/main.py finalize` 增量刷新。

**阶段 2（480–780 秒，≤25 请求）——复核与交叉核对**
1. 对每条已落盘 finding 复核：重读对应原文片段，确认 quote 逐字、locator 准确、
   verdict 与引文语义一致、severity 匹配；修正或补充 basis。
2. 交叉核对摘要/正文/表格/图注的数值、样本量、显著性表述，矛盾处标
   `inconsistency` 且两条证据都记录并写入 meta 的 `conflicts`。
3. 无法确认的条目把 `review_status` 改为 `needs_review`，写入 `uncertainty` 或
   `pending_human`；未取得的来源在 `source_ref` 写 `not_verified`。
4. 填写 `/app/work/meta.json`：overall_verdict、sources、uncertainty、
   pending_human、limitations、errors、degradations、change_log；重跑 finalize。

**阶段 3（780–900 秒，≤11 请求）——收尾与自检**
1. `python -B scripts/main.py integrity` 重算哈希并验证 quote 逐字存在；
   修正 mismatch（quote 非逐字或定位不符）。
2. `python -B scripts/main.py selftest`；修正缺失必填键。
3. 最终 `python -B scripts/main.py finalize`；确认 status/verdict 正确，
   result.json 可被 `json.load` 解析、report.md 已生成。
4. 时间不足则保留当前 partial 产物并在 `limitations` 写明未完成项，绝不补造。

时间不够时优先保证：骨架已落盘 → 关键章节高危问题已落盘 → 每条有定位与逐字
摘录 → 状态诚实。宁缺毋滥。

## 5. 自检清单（逐条核对，对应评分维度）

- [ ] result.json 存在且 `json.load` 可解析；含 case_id 与 output_contract 全部字段。
- [ ] 每条 finding 必填键齐全：id/category/finding_type/severity/verdict/
      locator/quote/claim/basis/source_ref/suggestion/confidence/review_status。
- [ ] 每条结论能按 `locator + quote` 回查原文，quote 为逐字摘录。
- [ ] 每条 finding 在 evidence_map 中至少一条 primary_support 证据，quote_hash 正确。
- [ ] 冲突信息未隐藏：同一指标不一致时两条证据都在，conflicts 双侧引用。
- [ ] 判定与引文语义一致，"引用存在"与"引用支持"已区分。
- [ ] 七类检查项与方法/结果/讨论等关键章节均有覆盖或明确标注未覆盖。
- [ ] 无法确认的条目已标 needs_review 并进 uncertainty/pending_human。
- [ ] sources 只含实际核验过的来源；未取得写 not_verified，无虚构 DOI/URL。
- [ ] status 与 verdict 诚实；空材料为 insufficient_evidence。
- [ ] 脚本用标准库、--help 退出码 0、同一输入重复运行结果一致（无随机数）。

## 6. 降级策略

遇以下情况**不崩溃、不编造**，如实记录并继续处理其余材料：

| 情况 | 处置 |
|---|---|
| 空输入目录/无材料 | status=`insufficient_evidence`，verdict=`insufficient`，limitations 列缺口 |
| 坏 JSON / 坏编码 | 记录到 errors/degradations，跳过该文件，继续读其它材料 |
| 缺 pdftotext | manifest 记 degradation，该 PDF 标 needs_ocr，提示改走 OCR |
| 扫描版 PDF（抽出空文本） | 标 needs_ocr；无法识别则不判结论 |
| 只有摘要/原文取不到 | 相关结论标 undeterminable/needs_review，不硬判 |
| 引文严重歧义/来源版本不一致 | 两条都记录并标 source_conflict，进 conflicts/uncertainty |
| 写盘失败 | 尝试降级到最小合法产物；仍失败则写 stderr 并退出非 0 |
| 数据库/权限不足 | 记录 limitations，该来源写 not_verified |

任何"缺失"写 `not_reported`/`not_verified`，禁止虚构引用、DOI、数值、来源或
撤稿状态；禁止编造评测分数与运行结果。

## 7. 最小调用示例（示意，不冒充正式结果）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py discover                 # 扫描 /input
python3 -B scripts/main.py scaffold                 # 先落 partial 骨架
# …Agent 写 /app/work/findings/F0001.json…/meta.json…
python3 -B scripts/main.py finalize                 # 合并 -> result.json + report.md
python3 -B scripts/main.py integrity                # 验证 quote 哈希与逐字存在
python3 -B scripts/main.py selftest                 # 包级自检
```

本地复跑可用 `--input/--output/--work` 指定目录；`scripts/selftest.py` 可单独运行。
