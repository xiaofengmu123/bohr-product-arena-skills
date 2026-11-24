---
name: scientific-db-query-analysis
description: 科研数据库查询与分析：只读连接 SQLite/CSV 数据快照，自动核对 schema 与字段语义、推断跨表连接键，执行可复算的清洗、统计、可视化与跨表查询，交付带证据溯源的 result.json。当任务涉及数据库连接、SQL 查询、结果抽取、字段解释或跨表分析时使用。
version: 1.1.0
---

# 科研数据库查询与分析 Skill

## 1. 角色与何时使用

本 Skill 用于「科研数据库查询与分析」类任务：读取授权数据快照（SQLite `.db/.sqlite/.sqlite3/.db3` 或 `.csv/.tsv/.tab`）、数据字典与任务书，完成只读连接、schema 检索、清洗、统计、可视化与跨表查询，交付可复核的结果与规则。核心要求：查询能在授权快照上直接执行、结果能与独立复算对上、字段含义与关联路径可被他人按记录复核。

适用：任务要求对固定科研数据完成清洗/统计/可视化/多步分析/数据库查询，且必须保留计算依据与证据定位。
不适用：无数据文件或任务书；需要写回/修改授权库；纯文本问答不产出结构化结果。本 Skill 只做只读查询。

## 2. 输入契约

- 运行时事实来源在 `/input/`：`case.json`（案例标识 `case_id` 与题目 `output_contract`）、`task.md`（任务说明、数据字典口径与完成标准），以及数据快照（`*.db/*.sqlite/*.sqlite3/.db3` 或 `*.csv/.tsv/.tab`）和数据字典（任意 `.md/.csv/.yaml`）。
- 不假设固定文件名/键名：脚本扫描 `/input` 全部文件；`case.json` 缺失时回退到任意 JSON。
- 工作目录 `/app/work`（中间产物、`queries.json`、`contract_answers.json`），最终产物写入 `/app/output/`。
- 文本按 `utf-8-sig→utf-8→gb18030→latin-1` 容错读取；单文本文件上限 2MB；坏编码/坏 JSON 记入 `errors`/`degradations` 后继续，不崩溃。

## 3. 输出契约（必须）

必须产出 `/app/output/result.json`，且始终合法可解析：

- `case_id`：原样取自 `case.json`；
- 题目 `output_contract` 的**全部字段**（缺值填 `null`/`[]`，不改名、不删未知键）；
- `status` ∈ `ok | partial | insufficient_evidence | input_error`（诚实标注，不夸大）；
- `sources`：每项 `{locator, quote, role}`，`quote` 为材料逐字片段；
- `uncertainty`：每项 `{item, level, reason}`；
- `change_log`：每项 `{ts, step, change, status}`，记录每次增量改动；
- 以及 `analysis`（schema/清洗/统计/相关/图表/建议连接键）、`query_log`（每条实际执行 SQL 与行数）、`answers`、`artifacts`、`errors`、`degradations`、`limitations`、`pending_human`。

主产物还包括：`report.md`、`evidence_map.json`（每条含 locator + 逐字 quote）、`schema.json`（含 `suggested_joins` 连接键推断）、`summary_stats.csv`、`data_quality.csv`（含 `coded_as_text`/`id_like` 标记）、`correlation_matrix.csv`、`query_log.csv`、`value_counts.json` 与 `figures/*.svg`（可编辑、可解析）。图表必须伴随底层数据，不得只交图不给数。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，按单个 case）

原则：每个阶段都先落盘再分析；前 3 分钟必须有一版合法 `result.json` 骨架；之后只做增量改进。若中途超时，`result.json` 已是合法 partial，直接以现状交付并标注未完成项。

- **阶段 0｜落骨架与自动分析（0–3 分钟，必须）**：
  在 skill 根目录执行
  `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
  该命令立即写入合法骨架（case_id、全部 contract 键=null、status=partial），随后自动完成只读连接、schema 检索、行数、缺失/重复检测、描述统计、Pearson 相关、连接键推断、SVG 图表与 report/evidence 产物。输入：/input 全部文件；输出：一版可交付 partial 结果。
- **阶段 1｜读题与字段解释（3–8 分钟）**：
  读 `/input/case.json` 与 `task.md`，列出题目要回答的具体问题及涉及库/表/字段/时间区间/筛选条件/跨表维度；**先读 `/app/output/schema.json` 与 `data_quality.csv`**，对照数据字典逐字段解释含义、单位与口径，识别易混字段（样本状态 vs 实验状态）与编码型字段（`coded_as_text`、文本数字），采用 `schema.json.suggested_joins` 作为跨表连接键起点。
- **阶段 2｜定向查询与增量改进（8–14 分钟）**：
  为每个问题写只读 SQL，注意连接键、聚合粒度、去重、NULL 处理；编码字段转数值前显式说明转换规则。把查询以 `[{"id":"q1","sql":"...","purpose":"..."}]` 追加到 `/app/work/queries.json`，重跑阶段 0 同一命令（幂等：只新增/更新对应条目，不覆盖已产出）。答案需人工判定时，写入 `/app/work/contract_answers.json`（映射 contract 键→值）后重跑，由脚本合并进 result.json。
- **阶段 3｜复核与语义解释（14–15 分钟）**：
  关键数值独立复算（SQL 聚合 vs Python 重算对照，脚本已自动输出 `matches_python`）；核对行数未因漏连接条件膨胀；把字段口径、冲突、异常写入 `uncertainty`/`pending_human`；补齐 contract 字段并更新 `change_log`。
- **阶段 4｜自检收口（15 分钟内）**：
  执行第 5 节清单；若仍缺项则保持 `partial` 并写明 limits，不得改报 ok。

约束：优先用脚本一次完成重活、少做多轮改写；复述数字前先读 `result.json`/`summary_stats.csv`，不要凭记忆。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：确实只读连接授权数据源；schema/行数/字段类型来自真实执行，不是手写。
- [ ] 实际效果：SQL 可复跑，聚合粒度与问题匹配；无漏连接条件导致行数膨胀；编码型字段未直接当数值聚合。
- [ ] 实际效果：覆盖任务要求的表、字段、时间/筛选/跨表维度；未覆盖部分写入 `limitations`。
- [ ] 可核验：`query_log` 每条含 SQL、行数、结果摘要；`evidence_map` 每条含 locator + 逐字 quote。
- [ ] 完整性：`result.json` 含 case_id + contract 全部字段 + status/sources/uncertainty/change_log。
- [ ] 可靠性：空输入→`insufficient_evidence` 且退出码 0；坏 JSON/坏编码→记入 errors/degradations 不崩溃；缺参数走默认路径。
- [ ] 稳定性：同一输入重复运行，除时间戳外分析数值一致（无随机、固定排序）。
- [ ] 异常处理：空结果、连接失败、权限不足、字段歧义、重复记录、类型不一致均显式记录，不静默掩盖。
- [ ] 生态：`references/` 中的 SQL 模板与检查规则可被他人复用复跑。

评分维度与上述自检项的逐条映射见 `references/rubric-map.md`；红线与 SQL 模板见 `references/checklist.md`。

## 6. 降级与诚实标注（硬规则）

- 缺数据：无 DB/CSV → `status=insufficient_evidence`，`pending_human` 列缺口；退出码仍为 0。
- 缺软件：仅用 Python 标准库；某统计因样本不足无法计算时写 `null` 并注明，不补造。
- 结果无法解释：记入 `uncertainty` 并标 `source_conflict`；两来源不一致时两者都记录。
- 严禁编造评测分数、运行日志、连接参数、DOI、数值或「已通过」的自检结论；未执行的步骤不得写为已执行。
- 写盘失败：降级到最小合法 result.json（仅骨架），进程不崩溃。

## 7. 脚本调用

- `python3 -B scripts/main.py --help`：查看参数与默认路径。
- 主流程：`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
- 定向查询：写 `/app/work/queries.json` 后重跑同一命令，结果进入 `query_log` 与 `answers`。
- 补齐答案：写 `/app/work/contract_answers.json` 后重跑，脚本幂等合并 contract 字段。
- 自检：`python3 -B scripts/main.py --selftest`（8 项，退出码 0 为全过）。
- 脚本只依赖标准库；路径用相对 `scripts/...`（在 skill 根目录执行；若 Harness 工作目录不同，先 `cd /skills/weekly-skill`）。

## 8. 边界与交付结构

- 一个 Skill 只解决「只读科研数据快照的查询与分析」这一类任务，不覆盖写库、ETL 调度或大图渲染。
- 数据字典、任务书与数据快照是事实依据；不依据领域标签补造数据。
- 官方 3 个固定案例与白板由平台提供，本 Skill 不携带任何预写答案或正式测试产物；`--selftest` 仅在内存中生成合成演示数据。

交付结构：`SKILL.md`；`scripts/{main,dbkit,charts}.py`；`references/{rubric-map,checklist}.md`。
