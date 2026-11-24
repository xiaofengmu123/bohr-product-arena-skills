---
name: deterministic-lit-screening
description: 确定性文献检索与纳排筛选 Skill：把固定题录跑成可复现的筛选闭环，输出逐条纳排判定、理由码、逐字证据、去重与冲突仲裁，并以内置自检保证同一输入两次产物逐字节一致。当需要围绕科研问题在给定快照题录上检索、去重、双人纳排、冲突仲裁并留下可复核审计链时使用。
version: 1.0.0
---

# 确定性文献检索与纳排筛选

## 1. 角色与何时使用

本 Skill 在**给定快照题录**上完成"研究问题 → 检索协议 → 去重 → 标题/摘要双人纳排 →
冲突仲裁 → 审计计数闭合"的完整闭环，交付可被第三人复跑的结果与证据链。

**何时使用**：任务要求对一批给定文献逐条判断纳入/排除并保留依据、去重、处理标注分歧或
生成可复核流程日志时。**不做**：全文质量评价、数据提取、统计分析、论文写作。**绝不**
做实时数据库联网检索或声称全局召回——只处理输入中给出的有限题录（本题为固定研究问题
+ 九篇真实论文 + 实际查询输入 + 全量记录筛选）。

**本版设计取舍（自检与确定性）**：脚本只负责可复现的"账本"——输入发现、题录解析、去重、
证据子串校验、计数闭合、确定性序列化。科学判断（纳入/排除、理由码、证据片段、仲裁）由
模型负责并写入 `/app/work/judgments.json`。所有产物不含墙钟时间，改用**内容寻址 run_id +
单调步骤号**，因此同一输入与同一工作状态两次运行产出**逐字节一致**的 result.json；自检
脚本会重跑 finalize 并比对哈希验证这一点。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF/材料按题目给出，PDF
  用 Harness 的 PDF 文本工具读取，**不要**依据标签或领域名补造内容。
- 先扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md`、
  `*.csv/tsv/ris/bib/json/jsonl` 都要能识别；单文本文件 >2MB 跳过并记录。
- 从 `case.json` 中定位并**原样保存** `output_contract`（或 `outputContract` /
  `result_schema` / `result_fields` / `output`）；同时提取 `case_id`、研究问题、纳排标准、
  检索式。`task.md` 用于补充评分维度与运行契约。
- 工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构为：

1. `case_id`（来自 case.json/task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**（顶层键）：缺值给 `null`/`[]`，不改名、
   不删键；计算值经 `/app/work/contract_mapping.json` 映射回填；
3. 追加 `status`（`ok | partial | insufficient_evidence | input_error`）、`sources`、
   `uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`；
   再加 `research_question`、纳排标准、`deduplication`、`screening.records`（每条含
   `record_id/title/doi/decision/reason_code/evidence_quote/evidence_locator/
   evidence_verified/evidence_match_mode/reviewer_a/reviewer_b/conflict/arbitration/
   needs_human`）、`conflicts`、`audit`（各环节输入/输出条数与闭合标志）。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`、`evidence_map.json`（locator + 逐字 quote）。这些为
文本/表格；若平台另要求 Office/图产物，必须再附真实 PDF/PNG 预览并如实说明。

`output_contract` 的计算值回填方式：`/app/work/contract_mapping.json` 形如
`{"<合同字段>": {"value": <任意JSON>}}` 或 `{"<合同字段>": "ref:screening.records"}`；
支持的 `ref:` 前缀有 `screening.records` / `screening.include` / `screening.exclude` /
`deduplication.clusters` / `protocol.research_question` / `protocol.inclusion_criteria` /
`protocol.exclusion_criteria`。未映射的字段一律保持 `null`/`[]`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

严格按序执行；**前 3 分钟内先落盘一版 partial 骨架，之后只做增量改进**。

- **Step 0 — 落盘骨架（0–3 分钟，必备）**
  `python3 -B scripts/main.py skeleton`。输入 `/input`；输出 `/app/output/result.json`
  （status=partial 的合法骨架）与 `/app/work/{inventory,records,protocol}.json`。若脚本
  失败，立即手工写一版含 `case_id`、`status`、output_contract 空键、`change_log` 的骨架，
  绝不空手跑分析。
- **Step 1 — 提取协议（3–8 分钟）**　读 `inventory.json` 与 `case.json`/`task.md`，确定
  研究问题、纳入/排除标准、`criteria_version`，回写 `/app/work/protocol.json` 并置
  `criteria_frozen=true`。标准定版即冻结，不中途漂移。
- **Step 2 — 去重（8–12 分钟）**　`python3 -B scripts/main.py normalize`，读 `records.json`
  输出 `dedup.json` 与 `judgments_template.json`。核对：同研究多副本、预印本与正式版按
  DOI 或规范化标题合并并保留 `merge_reason`；标题相似度 0.85–0.95 只标
  `possible_duplicate` 留人工，不自动合并不同研究。
- **Step 3 — 逐条纳排（12–50 分钟，占主要请求预算）**　逐条读标题/摘要（必要时读 PDF），
  把判定写入 `/app/work/judgments.json`（先复制 template）：每条给 `decision`、
  `reason_code`、`evidence_quote`（标题/摘要逐字片段）与 `evidence_locator`。仅摘要、语言
  不符、对象部分重叠等边界走 `uncertain`/`needs_human`。支持双人独立判定，分歧写
  `conflict` 并仲裁到 `arbitration`，僵持即留 `uncertain`。
- **Step 4 — 冲突仲裁与复核（50–60 分钟）**　检查所有 `conflict.is_conflict=true` 且无
  仲裁的记录；复核 `exclude` 记录的 quote 确实指向真实片段；把裸"不相关"排除补上具体
  判据。
- **Step 5 — 汇总与自检（60–75 分钟）**
  `python3 -B scripts/main.py finalize` 再 `python3 -B scripts/main.py selfcheck`。
  `finalize` 合并判定、校验证据子串与计数闭合、写 result.json + 四件可编辑产物；
  `selfcheck` 逐项打印 PASS/FAIL 并验证确定性。然后把候选集、筛选结果、证据记录按
  `output_contract` 字段名回填（经 `contract_mapping.json`；缺数据保持 null/[]，不编造）。
  若计数不闭合，先修 `judgments.json` 再重跑 `finalize`，**不要手改 result.json**（手改
  会被 selfcheck 判为 DRIFT）。

## 5. 自检清单（逐条勾选，脚本可覆盖其中自动项）

- [ ] `result.json` 含 `case_id`、`status` 与 output_contract 全部字段（缺值 null/[]）
- [ ] 每条唯一记录都有 decision + reason_code；非 uncertain 有逐字 `evidence_quote`
- [ ] `finalize` 未报 `evidence_mismatch`；quote 在标题/摘要中命中
- [ ] 去重仅合并同一研究，且保留 `merge_reason`；`possible_duplicate` 未擅自合并
- [ ] `audit`：n_input − n_merged == n_unique == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有分类与仲裁记录，僵持条目 `needs_human=true`
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致、两次产物逐字节一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例

## 6. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`，列出缺口，退出码 0，不抛异常、
  不示例填充。
- **坏 JSON/坏编码/导出格式不一致**：捕获后写 `degradations`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*` 码 + `pending_human`，注明
  `not_reported`。
- **缺软件/脚本不可用**：脚本只用标准库；若仍不可用，按第 3 节 schema 手工产出
  result.json，并在 `change_log`/`errors` 说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留两条意见与仲裁依据，标 `source_conflict` 或
  `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **所有 status 必须诚实**：材料不足就 `insufficient_evidence`，部分完成就 `partial`。

**证据溯源**：每个关键结论都给 `locator`（`case.json:<key>`、`materials/<file>`、记录的
`title`/`abstract`）+ 逐字 `quote`；脚本校验 quote 为真实子串，未命中写入 `degradations`
并置 `needs_human=true`。冲突信息不隐藏：同一指标两个来源不一致时两者都记录并标
`source_conflict`。

**最小调用示例（示例，非正式案例）**：

```bash
python3 -B scripts/main.py skeleton
python3 -B scripts/main.py normalize
# 模型写入 /app/work/judgments.json 与 contract_mapping.json
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
python3 -B scripts/selftest.py   # 离线自检，无案例数据也可运行
```
