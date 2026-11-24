---
name: contract-first-literature-screening
description: 契约优先的系统性文献检索与纳排筛选 Skill：以 case.json 的 output_contract 为唯一事实源，先动态回显契约骨架再逐条纳排，完成去重、双人判定、冲突仲裁、证据溯源与计数闭合审计，产出字段与契约逐一对齐的 result.json。当需要围绕科研问题检索并筛选固定题录、保留纳入/排除依据、生成可被第三人复跑的结果与审计链时使用。
version: 1.1.0
---

# 契约优先的系统性文献检索与纳排筛选

## 1. 角色与何时使用

本 Skill 在**固定题录快照**上完成可复核的筛选闭环：研究问题 → 检索协议 → 去重 →
标题/摘要纳排（理由码 + 逐字证据）→ 双人冲突仲裁 → 审计计数闭合。它不替代研究者作最终
科学判断，不负责全文精读质量评价、数据提取、统计分析或论文写作；**绝不**做实时数据库联网
检索或声称全局召回，只处理给定快照（本题为固定研究问题 + 真实论文 + 实际查询输入 +
全量记录筛选）。

**何时使用**：任务要求检索并筛选文献、逐条判断纳入/排除并保留依据、去重、处理冲突或生成
可复核流程日志时。**不做**：全文精读评价、meta 分析数据提取、论文撰写。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF / 材料按题目给出，用
  Harness 的 PDF 文本工具读取，**不要**依据标签或领域名补造内容。
- 先扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md`、
  `*.csv/tsv/ris/bib/json/jsonl` 都要识别；单文本文件 >2MB 跳过并在 degradations 记录。
- 从 `case.json` 定位并**原样读取** `output_contract`（兼容 `outputContract` /
  `result_schema` / `result_fields` / `output`）；同时提取 `case_id`、研究问题、纳入/排除
  标准、检索式。`task.md` 用于补充评分维度与运行契约。
- 工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约（契约优先，本版核心）

本版与一般方案的差异：**output_contract 是唯一事实源**。脚本在第一步就递归解析契约、
动态生成字段骨架并把每条契约路径记录为 `contract_paths`，最终结果必须逐键对齐契约，缺值
给 `null`/`[]`，不改名、不删未知键。`change_log` 是一等公民（追加式账本），每个环节都写入
时间戳 + 动作 + before/after 计数，最终校验时间戳单调递增。

必须产出 `/app/output/result.json`，结构为：

1. `case_id`（来自 case.json / task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**（递归回显，标量叶子给 `null`，列表给 `[]`）；
3. 追加标准键：`status`（`ok | partial | insufficient_evidence | input_error`）、
   `sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、
   `runtime`；再追加 `research_question`、纳入/排除标准、`criteria_version`、
   `deduplication`、`screening.records`（每条含 `record_id/title/doi/decision/reason_code/
   evidence_quote/evidence_verified/reviewer_a/reviewer_b/conflict/arbitration/needs_human`）、
   `conflicts`、`audit`（各环节输入/输出条数与闭合标志）。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`。这些为文本/表格；若平台另要求 Office/图产物，必须再附
真实 PDF/PNG 预览并如实说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

严格按序执行，**前 3 分钟先落盘 partial 结果骨架，之后只做增量改进**：

**Step 0 — 落盘骨架（0–3 分钟，必备）**
```
cd /skills/weekly-skill && python3 -B scripts/pipeline.py skeleton
```
输入 `/input`，输出 `/app/output/result.json`（status=partial 的合法骨架，含契约字段回显）、
`/app/work/{inventory,records,protocol,contract}.json`。脚本失败就立即手工写一版骨架
（含 case_id、status、output_contract 空键、change_log），绝不空手跑分析。

**Step 1 — 提取协议（3–8 分钟）**
读 `inventory.json` 与 `case.json`/`task.md`，确定研究问题、纳入/排除标准、
`criteria_version`，回写 `/app/work/protocol.json`。标准一旦定版即冻结，不中途漂移。

**Step 2 — 去重（8–12 分钟）**
```
python3 -B scripts/pipeline.py normalize
```
读 `records.json`，输出 `dedup.json` 与 `judgments_template.json`。同一研究多来源副本、
预印本与正式版按 DOI / 规范化标题合并；标题相似（0.85–0.95）只标 `possible_duplicate`
留人工，不自动合并不同研究。

**Step 3 — 逐条纳排（12–50 分钟，占主要请求预算）**
逐条读标题/摘要（必要时读 PDF），判定写入 `/app/work/judgments.json`（先复制 template）：
每条给 `decision`、`reason_code`、`evidence_quote`（标题/摘要逐字片段）。仅摘要、语言不符、
对象部分重叠等边界情形走 `uncertain`/`needs_human`。支持双人独立判定，分歧写 `conflict`
并仲裁到 `arbitration`，僵持即留 `uncertain`。

**Step 4 — 冲突仲裁与复核（50–60 分钟）**
检查 `conflict.is_conflict=true` 且无仲裁的记录；复核 `exclude` 记录的 `evidence_quote`
确实指向真实片段；把“不相关”的裸排除补上具体判据。

**Step 5 — 汇总与自检（60–75 分钟）**
```
python3 -B scripts/pipeline.py finalize
python3 -B scripts/pipeline.py selfcheck
```
`finalize` 合并判定、校验证据子串、把契约字段按路径逐键回填、校验计数闭合与 change_log
单调性，写最终 `result.json` + 三件可编辑产物。`selfcheck` 逐项打印 PASS/FAIL。剩余时间
用于增量改进，**不**用于编造数据。

## 5. 自检清单（逐条勾选）

- [ ] `result.json` 含 `case_id`、`status` 与 output_contract 全部字段（缺值 null/[]）
- [ ] `selfcheck` 的 contract 校验无 missing_contract_keys
- [ ] 每条唯一记录有 decision + reason_code，非 uncertain 有逐字 evidence_quote
- [ ] `finalize` 未报 evidence mismatch；证据片段在标题/摘要中可命中
- [ ] 去重仅合并同一研究，保留 merge_reason；possible_duplicate 未擅自合并
- [ ] `audit`：检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有分类与仲裁记录，僵持条目 `needs_human=true`
- [ ] `change_log` 非空且时间戳/序号单调递增（追加式账本）
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例

## 6. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`，列出缺口，退出码 0，不抛异常、不示例填充。
- **坏 JSON/坏编码/数据库导出格式不一致**：捕获后写 `degradations`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*` 码 + `pending_human`，注明 `not_reported`。
- **缺软件**：脚本只用标准库；若脚本不可用，按第 3 节 schema 手工产出 result.json，在
  `change_log`/`errors` 说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留两条意见与仲裁依据，标 `source_conflict` 或 `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **所有 status 必须诚实**：材料不足就 `insufficient_evidence`，部分完成就 `partial`。
- **契约缺 output_contract**：`status=partial`，在 `errors` 写明“无输出契约”，仍交付可编辑产物。
