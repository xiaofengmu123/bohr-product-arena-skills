---
name: systematic-literature-screening
description: 系统性文献检索与纳排筛选 Skill（证据溯源强化版）：把研究问题转成可执行检索协议，对固定快照题录做去重、标题摘要双人纳排、冲突仲裁与可复核审计，每条结论携带结构化 locator + 逐字 quote 的细粒度 evidence_map。当需要围绕科研问题检索并筛选文献、保留纳入/排除依据、产出可追溯候选集与计数闭合流程日志时使用。
version: 1.1.0
---

# 系统性文献检索与纳排筛选（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 完成"固定快照题录上的系统性筛选闭环"：研究问题 → 检索协议 → 去重 → 标题/摘要纳排
（理由码 + 逐字证据）→ 双人冲突仲裁 → 审计计数闭合。**何时使用**：任务要求检索并筛选文献、
逐条判断纳入/排除并保留依据、去重、处理冲突或生成可复核流程日志时。**不做**：全文精读质量评价、
数据提取、统计分析、论文写作；**绝不**做实时数据库联网检索或声称全局召回——只处理给定快照题录。

科学判断由模型负责；脚本只负责可复现的账本（发现输入、回填输出契约、规范化、去重键、
证据子串校验、计数闭合、诚实状态）。凡不能确定的，一律标 `uncertain` / `pending_human`，
不得编造文献、DOI、数值或结论。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF / 材料按题目给出，PDF 用
  Harness 的 PDF 文本工具读取，**不要**依据标签或领域名补造内容。
- 先扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md`、
  `*.csv/tsv/ris/bib/json/jsonl` 都要能识别；单文本文件 >2MB 跳过并记入 degradations。
- 从 `case.json` 定位并**原样读取** `output_contract`（或 `outputContract` / `result_schema` /
  `result_fields` / `output` / `output_schema`）；同时提取 `case_id`、研究问题、纳入/排除标准、
  检索式。`task.md` 用于补充评分维度与运行契约。
- 中间产物写 `/app/work`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，内容为：

1. `case_id`（来自 case.json / task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**：缺值给 `null` 或 `[]`，不改名、不删键；
3. 追加 `status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、
   `uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、
   `runtime`；再加 `research_question`、纳入/排除标准、`deduplication`、`screening.records`
   （每条含 `record_id/title/doi/decision/reason_code/evidence_quote/evidence_verified/locator/
   reviewer_a/reviewer_b/conflict/arbitration/needs_human`）、`conflicts`、`audit`、`evidence_map`。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`。这些为文本/表格，无需 PDF 预览；若平台另要求 Office/图产物，
必须再附真实 PDF/PNG 预览并如实说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

严格按序执行，**前 3 分钟内先落盘结果骨架**，之后只做增量改进：

**Step 0 — 落盘骨架（0–3 分钟，必备）**

```bash
cd /skills/weekly-skill && python3 -B scripts/main.py skeleton
```

输入 `/input`；输出 `/app/output/result.json`（status=partial 的合法骨架）、
`/app/work/{inventory,records,protocol,contract}.json`。若脚本失败，立即手工写一版 `result.json`
骨架（含 case_id、status、output_contract 空键、change_log），绝不空手跑分析。

**Step 1 — 提取协议（3–8 分钟）**　读 `inventory.json` 与 `case.json`/`task.md`，确定研究问题、
纳入/排除标准、criteria_version；必要时回写 `/app/work/protocol.json`。标准一旦定版即冻结。

**Step 2 — 去重（8–12 分钟）**

```bash
python3 -B scripts/main.py normalize
```

读取 `records.json`，输出 `dedup.json` 与 `judgments_template.json`。核对去重：同一研究多来源副本、
预印本与正式版按 DOI/规范化标题合并；标题近似（0.85–0.95）只标 `possible_duplicate` 留人工，
不自动合并不同研究。

**Step 3 — 逐条纳排（12–50 分钟，占主要请求预算）**　逐条读标题/摘要（必要时读 PDF），把判定写入
`/app/work/judgments.json`（先复制 template）：每条给 `decision`、`reason_code`、`evidence_quote`
（标题/摘要逐字片段）。仅摘要、语言不符、对象部分重叠等边界情形走 `uncertain`/`needs_human`。
支持双人独立判定，分歧写 `conflict` 并仲裁到 `arbitration`，僵持即留 `uncertain`。

**Step 4 — 冲突仲裁与复核（50–60 分钟）**　检查所有 `conflict.is_conflict=true` 且无仲裁的记录；
复核 `exclude` 记录的 `evidence_quote` 确实指向真实片段；把"不相关"的裸排除补上具体判据。

**Step 5 — 汇总与自检（60–75 分钟）**

```bash
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
```

`finalize` 合并判定、校验证据子串与计数闭合、写最终 `result.json` + 三件可编辑产物；`selfcheck`
逐项打印 PASS/FAIL。然后把候选集、筛选结果、证据记录按题目 `output_contract` 字段名回填到
`result.json` 对应键（脚本已把这些键以 null/[] 占位，缺数据保持 null/[]，不编造）。若计数不闭合，
先修 `judgments.json` 再重跑 `finalize`，不改脚本掩盖问题。

## 5. 证据溯源（本版核心设计）

- 每条关键结论（decision、reason_code、dedup_merge、conflict 仲裁）都带 `locator` + 逐字 `quote`。
- `locator` 为结构化对象：`{"file": "...", "pointer": "case.json#records[2].abstract"}`，可指回材料
  的精确位置，支持第三人独立复跑。
- 顶层 `evidence_map` 按 `evidence_id` 细粒度索引：每条含 `record_id / field / locator / quote /
  verification`；`verification` 记录命中模式（`exact | ws_normalized | ws_casefold | not_found`）与
  命中字段（title/abstract/doi/journal），把"逐字"与"宽松匹配"分开标注，不混淆。
- 去重合并、冲突仲裁同样入 `evidence_map`，保留 `cluster_members` 与合并理由，供独立复核。

## 6. 自检清单（逐条勾选）

自动检查：`python3 -B scripts/selftest.py`（同时校验 SKILL.md frontmatter、脚本语法与 result.json）
或 `python3 -B scripts/main.py selfcheck`（仅校验 result.json）。

- [ ] `result.json` 含 `case_id` 与 output_contract 全部字段（缺值 null/[]）
- [ ] 每条唯一记录都有 decision + reason_code；非 uncertain 有逐字 evidence_quote
- [ ] `finalize` 未报 evidence mismatch；证据片段在标题/摘要中可命中
- [ ] 去重仅合并同一研究，保留 merge_reason；possible_duplicate 未擅自合并
- [ ] `audit`：检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有分类与仲裁记录，僵持条目 `needs_human=true`
- [ ] `evidence_map` 每条有 locator + quote + verification
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例

## 7. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`，列出缺口，退出码 0，不抛异常、不示例填充。
- **坏 JSON/坏编码/导出格式不一致**：捕获后写 `degradations`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*`/`un_*` 码 + `pending_human`，
  注明 `not_reported`。
- **缺软件**：脚本只用标准库；若脚本不可用，按第 3 节 schema 手工产出 result.json，并在
  `change_log`/`errors` 中说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留两条意见与仲裁依据，标 `source_conflict` 或 `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **所有 status 必须诚实**：材料不足 `insufficient_evidence`，部分完成 `partial`。

## 8. 边界

本 Skill 只处理给定快照题录，不联网补召、不声称全局召回、不替代研究者最终科学判断；全文精读
质量评价、数据提取、统计分析、论文写作不在范围内。示意输出（如有）必须标注为示例，不冒充正式案例结果。
