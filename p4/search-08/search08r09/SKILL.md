---
name: systematic-literature-screening
description: 系统性文献检索与纳排筛选 Skill：把研究问题转成可执行检索协议，在固定题录上完成去重、标题/摘要逐条纳排、双人冲突仲裁与计数闭合的审计，每条结论保留理由码与逐字证据片段。当任务要求检索并筛选文献、保留纳入/排除依据、处理重复与冲突、产出可复跑流程日志时使用。
version: 1.1.0
---

# 系统性文献检索与纳排筛选

## 1. 角色与何时使用

本 Skill 完成「固定题录上的筛选闭环」：研究问题 → 检索协议 → 去重 → 标题/摘要纳排
（理由码 + 逐字证据）→ 双人冲突仲裁 → 审计计数闭合，产出可被第三人复跑的结果与证据链。

**何时使用**：任务要求检索并筛选文献、逐条判断纳入/排除并保留依据、去重、处理冲突或
生成可复核流程日志时。**不做**：全文精读质量评价、数据提取、统计分析、论文写作；**绝不**
做实时数据库联网检索或声称全局召回——只处理给定快照题录（本题为固定研究问题 + 九篇
真实论文 + 实际查询输入 + 全量记录筛选）。

科学判断由模型负责；脚本只负责可复现账本（解析、规范化、去重键、证据子串校验、计数
闭合、诚实状态）。不能确定的，一律标 `uncertain` / `pending_human`，不编造文献或结论。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF 用 Harness 的 PDF
  文本工具读取，**不要**依据标签或领域名补造内容。先扫描输入目录，不假设固定文件名/键名。
- 从 `case.json` 定位并原样读取 `output_contract`（或 `outputContract` / `result_schema` /
  `result_fields` / `output`），同时提取 `case_id`、研究问题、纳入/排除标准、检索式；`task.md`
  补充评分维度与运行契约。单文本文件 >2MB 跳过并记录。
- 工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，内容为：

1. `case_id`（来自 case.json / task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**：缺值给 `null` 或 `[]`，不改名、不删键；
3. 追加 `status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、
   `uncertainty`、`change_log`；再加 `research_question`、`inclusion_criteria`、
   `exclusion_criteria`、`criteria_version`、`search_query`、`deduplication`、
   `screening.records`（每条含 `record_id/title/doi/decision/reason_code/evidence_quote/
   evidence_verified/reviewer_a/reviewer_b/conflict/arbitration/needs_human`）、
   `conflicts`、`audit`（各环节输入/输出条数与闭合标志）、`errors`、`degradations`、
   `pending_human`、`runtime`。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`。这些为文本/表格，无需 PDF 预览；若平台另要求 Office/图
产物，必须再附真实 PDF/PNG 预览并如实说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

严格按序执行，**前 3 分钟内先落盘结果骨架，之后只做增量改进**：

**Step 0 — 落盘骨架（0–3 分钟，必备）**
```
cd /skills/weekly-skill && python3 -B scripts/pipeline.py skeleton
```
输入 `/input`；输出 `/app/output/result.json`（status=partial 的合法骨架）与
`/app/work/{inventory,records,protocol,result_skeleton}.json`。若脚本失败，立即手工写一版
`result.json` 骨架（含 case_id、status、output_contract 空键、change_log），绝不空手分析。

**Step 1 — 提取协议（3–8 分钟）** 读 `inventory.json` 与 `case.json`/`task.md`，确定研究
问题、纳入/排除标准、`criteria_version`、检索式，回写 `/app/work/protocol.json`。标准定版即
冻结，中途不漂移；如需改版，必须给新版本号并回改此前判定。

**Step 2 — 去重（8–12 分钟）**
```
python3 -B scripts/pipeline.py normalize
```
读 `records.json`，输出 `dedup.json` 与 `judgments_template.json`。同一研究多来源副本、预印本
与正式版按 DOI/规范化标题合并并留 `merge_reason`；标题近似（0.85–0.95）只标
`possible_duplicate` 留人工，不自动合并不同研究。

**Step 3 — 逐条纳排（12–50 分钟，占主要请求预算）** 逐条读标题/摘要（必要时读 PDF），把判定
写入 `/app/work/judgments.json`：每条给 `decision`、`reason_code`、`evidence_quote`（标题/摘要
逐字片段）。仅摘要、语言不符、对象部分重叠等边界情形走 `uncertain`/`needs_human`。支持双人
独立判定，分歧写 `conflict` 并仲裁到 `arbitration`，僵持即留 `uncertain`。

**Step 4 — 冲突仲裁与复核（50–60 分钟）** 检查所有 `conflict.is_conflict=true` 且无仲裁的
记录；复核 `exclude` 的 `evidence_quote` 确实指向真实片段；把「不相关」的裸排除补上具体判据。

**Step 5 — 汇总与自检（60–75 分钟）**
```
python3 -B scripts/pipeline.py finalize
python3 -B scripts/pipeline.py selfcheck
```
`finalize` 合并判定、校验证据子串与计数闭合、写最终 `result.json` + 三件可编辑产物；`selfcheck`
逐项打印 PASS/FAIL。再把候选集、筛选结果、证据记录按题目 `output_contract` 字段名回填到
`result.json`（脚本已以 null/[] 占位，缺数据就保持 null/[]，不编造）。计数不闭合先修
`judgments.json` 再重跑 `finalize`，不改脚本掩盖问题。剩余时间用于增量改进，**不**编造数据。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] `result.json` 含 `case_id` 与 output_contract 全部字段（缺值 null/[]）
- [ ] 每条唯一记录都有 decision + reason_code，非 uncertain 有逐字 evidence_quote
- [ ] `finalize` 未报 evidence mismatch；证据片段在标题/摘要中可命中
- [ ] 去重仅合并同一研究且保留 merge_reason；possible_duplicate 未擅自合并
- [ ] `audit`：检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有分类与仲裁记录，僵持条目 `needs_human=true`
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例

## 6. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`，列出缺口，退出码 0，不抛异常、不示例填充。
- **坏 JSON/坏编码/数据库导出格式不一致**：捕获后写 `degradations`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*` 码 + `pending_human`，注明
  `not_reported`。
- **缺软件**：脚本只用标准库；若脚本不可用，按第 3 节 schema 手工产出 result.json，并在
  `change_log`/`errors` 中说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留两条意见与仲裁依据，标 `source_conflict` 或 `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **所有 status 必须诚实**：材料不足就 `insufficient_evidence`，部分完成就 `partial`。

## 7. 最小调用示例（示例，非正式案例）

```bash
python3 -B scripts/pipeline.py skeleton    # 落盘骨架
python3 -B scripts/pipeline.py normalize   # 去重 + 判定模板
# Agent 在 judgments.json 中填入 decision/reason_code/evidence_quote
python3 -B scripts/pipeline.py finalize    # 写最终 result.json + 三件产物
python3 -B scripts/pipeline.py selfcheck   # 自检
python3 -B scripts/selftest.py             # 鲁棒性场景自检
```

`main.py` 为等价薄入口：`python3 -B scripts/main.py <子命令>`。
