---
name: systematic-literature-screening
description: 系统性文献检索与纳排筛选 Skill：把固定题录上的研究问题转成可复核的筛选闭环，通过结构化中间表示（plan → records → dedup → judgments → evidence_map）完成去重、标题摘要双人纳排、冲突仲裁与计数闭合审计。当任务要求检索并筛选文献、保留纳入/排除依据、处理重复与冲突、产出可追溯候选集与流程日志时使用。
version: 1.1.0
---

# 系统性文献检索与纳排筛选（中间表示可复核版）

## 1. 角色与何时使用

本 Skill 在**给定快照题录**上完成“研究问题 → 检索协议 → 去重 → 标题/摘要纳排 → 双人仲裁 →
计数闭合审计”的闭环。**何时使用**：任务要求检索并筛选文献、逐条判断纳入/排除并保留依据、
去重、处理冲突或生成可复核流程日志。**不做**：全文质量评价、数据提取、统计分析、论文写作；
**绝不**做实时联网检索或声称全局召回——只处理给定题录（本题为固定研究问题 + 九篇真实论文 +
实际查询输入 + 全量记录筛选）。

核心设计取舍：**每一阶段都把状态落成结构化中间产物**，形成一条可逐环节复跑、可 diff 的链
`plan.json → records.json → dedup.json → judgments.json → evidence_map.json`。科学判断由模型负责；
脚本只做确定性账本（规范化、去重键、证据子串校验、计数闭合、诚实状态）。模型不能确定的，
一律标 `uncertain` / `pending_human`，**不得编造文献、DOI、标题、数值或结论**。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF/材料按题目给出，PDF 用
  Harness 的 PDF 文本工具读取，**不要**依据标签或领域名补造内容。
- 先扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md`、
  `*.csv/tsv/ris/bib/json/jsonl` 都要能识别；单文本文件 >2MB 跳过并记录到 degradations。
- 从 `case.json` 定位并**原样回填** `output_contract`（或 `outputContract`/`result_schema`/
  `result_fields`/`output`）；同时提取 `case_id`、研究问题、纳入/排除标准、检索式。`task.md`
  用于补充评分维度与运行契约。
- 中间产物写 `/app/work`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，内容为：

1. `case_id`（来自 case.json/task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**：缺值给 `null` 或 `[]`，不改名、不删键；
3. 追加 `status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、
   `uncertainty`、`change_log`；再加 `research_question`、`criteria`、`search_protocol`、
   `deduplication`、`screening.records`（每条含 `record_id/title/doi/decision/reason_code/
   evidence_quote/evidence_locator/evidence_verified/reviewer_a/reviewer_b/conflict/arbitration/
   needs_human`）、`conflicts`、`audit`、`evidence_map`、`errors`、`degradations`、`pending_human`。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`、`plan.json`、`evidence_map.json`。这些为文本/表格；若平台另
要求 Office/图产物，必须再附真实 PDF/PNG 预览并如实说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

严格按序执行，**前 3 分钟内先落盘结果骨架，之后只做增量改进**：

**Step 0 — 落盘骨架（0–3 分钟，必备）**
```
python3 -B scripts/pipeline.py skeleton
```
输入 `/input`，输出 `/app/output/result.json`（`status=partial` 的合法骨架）与
`/app/work/{inventory,plan,records}.json`。脚本失败则立即手工写最小 `result.json` 骨架
（case_id、status、output_contract 空键、change_log），绝不空手跑分析。

**Step 1 — 提取协议（3–8 分钟）**
读 `inventory.json`、`case.json`、`task.md`，确定研究问题、纳入/排除标准，把每条标准改写成
**可判定判据**写入 `plan.json` 的 `criteria` 与 `criterion_judgeability`，并填 `search_protocol`
（库、字段、时间范围、语种、检索日期、检索式）。定版即冻结 `criteria_version=v1`，不中途漂移。

**Step 2 — 去重（8–12 分钟）**
```
python3 -B scripts/pipeline.py normalize
```
读 `records.json`，写 `dedup.json` 与 `judgments_template.json`。核对：同 DOI / 规范化标题一致
的预印本与正式版合并并留 `merge_reason`；标题近似（0.85–0.95）只标 `possible_duplicate` 留人工，
不自动合并不同研究。

**Step 3 — 逐条纳排（12–50 分钟，占主要请求预算）**
逐条读标题/摘要（必要时读 PDF），把判定写入 `/app/work/judgments.json`（先复制 template）：
每条给 `decision`、`reason_code`、`evidence_quote`（标题/摘要逐字片段，理由码见
`references/reason_codes.md`）。仅摘要、语言不符、对象部分重叠等边界走 `uncertain`/`needs_human`。
支持双人独立判定，分歧写 `conflict`（type：decision/reason_code/evidence）并仲裁到 `arbitration`，
僵持即留 `uncertain`。

**Step 4 — 冲突仲裁与复核（50–60 分钟）**
检查所有 `conflict.is_conflict=true` 且无仲裁的记录；复核 `exclude` 记录的 `evidence_quote` 确实
指向真实片段；把裸“不相关”的排除补上具体 `ex_*` 判据。

**Step 5 — 汇总与自检（60–75 分钟）**
```
python3 -B scripts/pipeline.py finalize
python3 -B scripts/pipeline.py selfcheck
```
`finalize` 合并判定、校验证据子串与计数闭合、写 `result.json` + 五个可编辑产物；`selfcheck` 逐项
打印 PASS/FAIL。然后把候选集、筛选结果、证据记录按题目 `output_contract` 字段名回填（脚本已以
null/[] 占位，缺数据保持 null/[]，不编造）。计数不闭合先修 `judgments.json` 再重跑 `finalize`，
不改脚本掩盖问题。剩余时间用于增量改进，**不**用于编造数据。

## 5. 自检清单（逐条勾选）

- [ ] `result.json` 含 `case_id` 与 output_contract 全部字段（缺值 null/[]）
- [ ] 每条唯一记录都有 decision + reason_code，非 uncertain 有逐字 evidence_quote
- [ ] `finalize` 未报 evidence mismatch；证据片段在标题/摘要中可命中
- [ ] 去重仅合并同一研究，且保留 merge_reason；possible_duplicate 未擅自合并
- [ ] `audit`：检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有分类与仲裁记录，僵持条目 `needs_human=true`
- [ ] 中间表示链完整：plan/records/dedup/judgments/evidence_map 均落盘且可复跑
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例

## 6. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`（骨架阶段可为 `partial`），列出缺口，退出码 0，
  不抛异常、不示例填充。
- **坏 JSON/坏编码/数据库导出格式不一致**：捕获后写 `degradations`/`errors`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*` 码 + `pending_human`，注明 `not_reported`。
- **缺软件**：脚本只用标准库；若脚本不可用，按第 3 节 schema 手工产出 result.json，并在
  `change_log`/`errors` 中说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留两条意见与仲裁依据，标 `source_conflict` 或 `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **所有 status 必须诚实**：材料不足就 `insufficient_evidence`，部分完成就 `partial`。
