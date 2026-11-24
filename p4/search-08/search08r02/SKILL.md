---
name: systematic-literature-screening
description: 时间预算驱动的系统性文献检索与纳排筛选 Skill：把研究问题冻结为可执行检索协议，对固定快照题录执行去重、标题摘要双人纳排、分歧仲裁与可复核审计，逐条输出理由码与逐字证据片段，各环节计数闭合；先落盘部分结果骨架，再按时钟预算增量改进。当需要围绕科研问题检索筛选文献、保留纳入/排除依据、产出可追溯候选集与计数闭合日志时使用。
version: 1.1.0
---

# 系统性文献检索与纳排筛选（时间预算 + 早落盘）

## 1. 角色与何时使用

本 Skill 在“固定快照题录”上完成可复核的筛选闭环：研究问题 → 冻结版检索协议 → 去重 →
标题/摘要双人纳排（理由码 + 逐字证据）→ 分歧仲裁 → 审计计数闭合。**何时使用**：任务要求
检索并筛选文献、逐条判定纳入/排除并保留依据、去重、处理冲突或生成可复核日志时。

**不做**：全文质量评价、数据提取、统计分析、论文写作；**绝不**做实时数据库联网检索或
声称全局召回——只处理题目给定的快照材料（固定研究问题 + 真实论文 + 实际查询输入 + 全量筛选）。
科学判断由 Agent 完成；脚本只负责可复现的账本（规范化、去重键、证据子串校验、计数闭合、
时间预算与检查点）。Agent 不能确定的，一律标 `uncertain` / `pending_human`，不编造。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；论文 PDF/材料按题目给出，
  PDF 用 Harness 的 PDF 文本工具读取，**不要**依据标签或领域名补造内容。
- 先扫描输入目录，不假设固定文件名/键名：`case.json`、`materials/`、`notes.md`、
  `*.csv/tsv/ris/bib/json/jsonl/txt/md` 都要能识别；单文件 >2MB 跳过并记录。
- 从 `case.json` 定位并原样读取 `output_contract`（兼容 `outputContract` /
  `result_schema` / `result_fields` / `output`）；同时提取 `case_id`、研究问题、
  纳入/排除标准、检索式、题录记录。`task.md` 补充评分维度与运行契约。
- 中间产物写 `/app/work`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，内容为：

1. `case_id`（来自 case.json / task.md，缺失写 `"unknown"`）；
2. 题目 `output_contract` 的**全部字段原样回填**：缺值给 `null` 或 `[]`，不改名、不删键；
3. 追加 `status`（`ok | partial | insufficient_evidence | input_error`）、`artifacts`、
   `sources`、`uncertainty`、`pending_human`、`change_log`；再追加 `research_question`、
   纳排标准、`retrieval`、`deduplication`、`screening.records`（每条含 `record_id /
   title / doi / decision / reason_code / evidence_quote / evidence_locator /
   evidence_verified / reviewer_a / reviewer_b / conflict / arbitration / needs_human`）、
   `conflicts`、`audit`、`errors`、`degradations`、`runtime`。

可编辑主产物（都在 `/app/output/`）：`screening_table.csv`（逐条判定，utf-8-sig）、
`audit_log.json`、`protocol.md`。这些为文本/表格；若平台另要求 Office/图产物，必须再附
真实 PDF/PNG 预览并如实说明。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**总则**：严格按序执行；**前 3 分钟（≤180s）必须先落盘一版合法 partial 骨架**，之后只做
增量改进。每个阶段有独立秒数预算与检查点（`/app/work/checkpoint.json`）；阶段完成即落盘，
任何中断都不留空产物。64 次请求是硬预算：逐条判断尽量批量，边界条目才单次追问；每发一次
分析请求就 `log-request` 记一次账（仅自记，不冒充平台数据）。

| 阶段 | 秒数 | 脚本动作 | Agent 动作 |
|---|---|---|---|
| 0 skeleton | 150 | `python3 -B scripts/main.py skeleton` | 无需干预；失败则手工写骨架 |
| 1 protocol | 90 | `python3 -B scripts/main.py protocol` | 核对/回写 `protocol.json` 并冻结版本 |
| 2 normalize | 120 | `python3 -B scripts/main.py normalize` | 核对去重，标记 `possible_duplicate` |
| 3 screen | 330 | `python3 -B scripts/main.py screen`（进度检查） | 逐条写入 `judgments.json` |
| 4 arbitrate | 60 | `python3 -B scripts/main.py arbitrate` | 分类分歧并仲裁，僵持留 `uncertain` |
| 5 finalize | 90 | `python3 -B scripts/main.py finalize` | 核对闭合与证据命中 |
| 6 selfcheck | 60 | `python3 -B scripts/main.py selfcheck` | 按结果修 `judgments.json` 后重跑 |

阶段预算合计 900s；`timebox` 随时打印剩余预算与建议降级路径。标准一旦定版即冻结（在
`protocol.json` 置 `frozen=true` 并写 `criteria_version`），不中途漂移；若标准变更，
早期已筛记录必须回改并在 `change_log` 记录。

## 5. 自检清单（逐条勾选）

- [ ] `result.json` 含 `case_id` 与 output_contract 全部字段（缺值 null/[]）
- [ ] 每条唯一记录都有 decision + reason_code，非 uncertain 有逐字 evidence_quote
- [ ] `finalize` 未报 evidence mismatch；证据片段在标题/摘要中可命中
- [ ] 去重仅合并同一研究（DOI/规范化标题），`possible_duplicate` 未擅自合并
- [ ] `audit`：检索数 − 去重合并数 == 唯一数 == 纳入 + 排除 + 待定（闭合）
- [ ] 冲突均有类型与仲裁记录，僵持条目 `needs_human=true`
- [ ] `selfcheck` 全部 PASS；重复运行去重与计数一致（确定性）
- [ ] 未编造 DOI/标题/数值/评测分数；示意输出已标注为示例
- [ ] 阶段预算与请求记账如实落盘，未超预算（超则写 `degradations`）

## 6. 降级策略（不允许编造）

- **缺数据/空输入**：`status=insufficient_evidence`，列出缺口，退出码 0，不示例填充。
- **坏 JSON/坏编码/导出格式不一致**：捕获后写 `degradations`，继续处理其余材料。
- **DOI 缺失/摘要为空/仅摘要**：写 `uncertain` 或对应 `ex_*` 码 + `pending_human`，注 `not_reported`。
- **缺软件/脚本不可用**：脚本只用标准库；仍不可用时按第 3 节 schema 手工产出 result.json，
  并在 `change_log`/`errors` 说明，不得假装运行成功。
- **标准版本冲突/标注者僵持**：保留双方意见与仲裁依据，标 `source_conflict` 或 `uncertain`。
- **写盘失败**：降级到最小合法 result.json（骨架），不让进程崩溃。
- **超预算**：优先保证去重/计数闭合等可降级环节，再逐条精读；超时如实写入 `runtime` 与 `degradations`。

## 7. 边界

本 Skill 只解决“固定题录筛选闭环”这一类任务，不追求通用大而全。没有真实材料时输出
“材料不足”，不做示例填充。离线自检见 `python3 -B scripts/selftest.py`（空输入/坏 JSON/
正常案例三组合成夹具，均为示例，非正式案例答案）。
