---
name: scientific-db-analysis-ir
description: 科研数据库查询与分析：只读连接 SQLite/CSV 数据快照，读取数据字典与任务书，产出结构化中间表示（plan.json/figure_model.json）与可复核 result.json。覆盖连接、schema 检索、字段语义解释、清洗、统计、相关分析、可视化与跨表查询，每步保留 locator + 逐字 quote。当任务涉及数据库连接、SQL 查询、结果抽取、字段解释或跨表分析时使用。
version: 1.0.0
---

# 科研数据库查询与分析 Skill（中间表示与可复核版）

## 1. 角色与何时使用
本 Skill 面向「科研数据库查询与分析」：读取授权只读数据快照（`.db/.sqlite/.sqlite3/.db3` 或 `.csv/.tsv`）、数据字典与任务书，完成连接、schema 检索、字段语义解释、清洗、统计、可视化与跨表查询，交付可被他人按记录复核的结果与规则。核心判定标准只有三条：查询能在授权快照上直接执行、结果能与独立复算对上、字段含义与关联路径能按记录回查。

适用：对固定科研数据做清洗/统计/可视化/多步分析/数据库查询，且必须保留计算依据与证据定位。
不适用：无任何数据文件或任务书；需要写回或修改授权库；纯文本问答不产出结构化结果。本 Skill 只做只读查询。

## 2. 输入契约
- 运行时事实来源在 `/input/`：`case.json`（案例标识 `case_id` 与题目 `output_contract`）、`task.md`（任务说明、数据字典口径、完成标准），以及数据快照（`*.db/*.sqlite/*.sqlite3/.db3` 或 `*.csv/.tsv`）和数据字典（任意 `.md/.csv/.yaml/.json`）。
- 不假设固定文件名/键名：扫描 `/input` 全部文件；`case.json` 缺失时回退到任意 `.json`，仍缺失则 `case_id=null`。
- 工作目录 `/app/work`（中间产物、agent 追加的 `queries.json` 与 `plan.json` 工作副本），最终产物写入 `/app/output/`。
- 文本按 `utf-8-sig→utf-8→gb18030→latin-1` 容错读取；单文本文件上限 2MB；坏编码/坏 JSON 记入 `errors`/`degradations` 后继续，不崩溃。

## 3. 输出契约（必须）
必须产出 `/app/output/result.json`，始终合法可解析：
- `case_id`：原样取自 `case.json`；
- 题目 `output_contract` 的**全部字段**（缺值填 `null`/`[]`，不改名、不删未知键）；
- `status` ∈ `ok | partial | insufficient_evidence | input_error`（诚实标注，不夸大）；
- `sources`：每项 `{locator, quote, role}`，`quote` 为材料逐字片段；
- `uncertainty`：每项 `{item, level, reason}`；
- `change_log`：每项 `{ts, step, change, status}`，记录每次增量改动；
- 以及 `analysis`、`query_log`（每条实际执行 SQL 与行数）、`answers`、`artifacts`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`、`ir`（中间表示引用）。

主产物还包括：`report.md`、`evidence_map.json`（每条含 locator + 逐字 quote）、`schema.json`、`plan.json`、`figure_model.json`、`summary_stats.csv`、`data_quality.csv`、`correlation_matrix.csv`、`value_counts.json`、`query_log.csv` 与 `figures/*.svg`（可编辑、可解析）。图表必须伴随底层数据与 figure 模型，不得只交图不给数。

## 4. 中间表示（本版核心设计取舍）
为把「可复核」落到机器可检查的层面，本 Skill 在 raw 数据与最终报告之间强制落两套结构化中间产物：

- **`plan.json`（分析计划）**：声明式记录本 case 的输入清单、contract 字段、从 task.md 抽取的问题/筛选/分组/跨表需求、每一步分析的输入输出与方法、字段语义解释（含义/单位/口径/编码规则/依据与置信度）、连接键候选与依据、校验规则、未决项。复核者不读代码即可知道「为什么这么算」。
- **`figure_model.json`（图形模型）**：每个图记录 `kind/title/data(底层表与列)/encoding/caption/evidence/svg`，图形只能由该模型加底层数据渲染。复核者可据此重渲染并核对「图与数一致」。

设计取舍：宁肯多写一份可读的结构化 IR，也不把推导过程埋进脚本日志；IR 与 result.json 引用同源，`evidence_map`/`sources` 中的每条结论都必须能指回 IR 或底层文件。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求，按单个 case 计）
原则：每个阶段先落盘再分析；**前 3 分钟必须有一版合法 `result.json` 骨架**；之后只做增量改进，不覆盖已产出。

- **阶段 0｜落骨架与自动分析（0–3 分钟，必须）**：在 skill 根目录执行
  `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
  该命令立即写入合法骨架（`case_id` + 全部 contract 键=null + `status=partial`），随后完成只读连接、schema 检索、行数、缺失/重复检测、描述统计、Pearson 相关、plan/figure IR、SVG 图表与 report/evidence 产物。输入：/input 全部文件；输出：一版可交付 partial 结果。
- **阶段 1｜读题与字段解释（3–8 分钟）**：读 `/input/case.json` 与 `task.md`，列出要回答的问题及涉及库/表/字段/时间区间/筛选/跨表维度；对照数据字典逐字段解释含义、单位与口径，识别易混字段（样本状态 vs 实验状态）与编码型字段（0/1、文本数字）。把结论写回 `plan.json` 的 `field_semantics`。
- **阶段 2｜定向查询与增量改进（8–14 分钟）**：为每个问题写只读 SQL，注意连接键、聚合粒度、去重、NULL 处理；编码字段转数值前在 plan 里显式写明转换规则。把查询以 `[{"id":"q1","sql":"...","note":"..."}]` 追加到 `/app/work/queries.json`，重跑阶段 0 同一命令（幂等：只新增/更新对应条目）。可用 `--queries /app/work/queries.json` 显式指定。
- **阶段 3｜复核与语义解释（14–15 分钟）**：关键数值独立复算（SQL 聚合 vs Python 重算，脚本自动输出 `matches_python`）；核对行数未因漏连接条件膨胀；把口径冲突、异常写入 `uncertainty`/`pending_human`；补齐 contract 字段并更新 `change_log`。
- **阶段 4｜自检收口（15 分钟内）**：执行第 6 节清单；仍缺项则保持 `partial` 并写 limits，不得改报 ok。若中途超时，`result.json` 已是合法 partial，直接以现状交付并标注未完成项。

约束：优先用脚本一次完成重活、少做多轮改写；复述数字前先读 `result.json`/`summary_stats.csv`/`plan.json`，不凭记忆。

## 6. 自检清单（对应评分维度，逐条勾选）
- [ ] 实际效果：确实只读连接授权数据源；schema/行数/字段类型来自真实执行，不是手写。
- [ ] 实际效果：SQL 可复跑，聚合粒度与问题匹配；无漏连接条件导致行数膨胀；编码型字段未直接当数值聚合。
- [ ] 实际效果：覆盖任务要求的表、字段、时间/筛选/跨表维度；未覆盖部分写入 `limitations`。
- [ ] 可核验：`query_log` 每条含 SQL、行数、结果摘要；`evidence_map`/`sources` 每条含 locator + 逐字 quote。
- [ ] 中间表示：`plan.json`/`figure_model.json` 存在、合法、可被 `scripts/selftest.py` 校验，且与 result.json 引用一致。
- [ ] 完整性：`result.json` 含 `case_id` + contract 全部字段 + `status/sources/uncertainty/change_log/ir`。
- [ ] 可靠性：空输入→`insufficient_evidence` 且退出码 0；坏 JSON/坏编码→记入 errors/degradations 不崩溃；缺参数走默认路径。
- [ ] 稳定性：同一输入重复运行，除时间戳外分析数值一致（无随机、固定排序）。
- [ ] 异常处理：空结果、连接失败、权限不足、字段歧义、重复记录、类型不一致均显式记录，不静默掩盖。
- [ ] 生态：`references/` 中的 SQL 模板与检查规则、`scripts/selftest.py` 可被他人复用复跑。

## 7. 降级与诚实标注（硬规则）
- 缺数据：无 DB/CSV → `status=insufficient_evidence`，`pending_human` 列缺口；退出码仍为 0。
- 缺软件：仅用 Python 标准库；某统计因样本不足无法计算时写 `null` 并注明，不补造。
- 结果无法解释：记入 `uncertainty` 并标 `source_conflict`；两来源不一致时两者都记录。
- 严禁编造评测分数、运行日志、连接参数、DOI、数值或「已通过」的自检结论；未执行的步骤不得写为已执行。
- 写盘失败：降级到最小合法 result.json（仅骨架），进程不崩溃。
- 对公共数据集的内置字段语义（`references/dataset-fields.md`）只是**待核对的先验**，必须与实际数据字典一致才可采信；不一致以数据字典为准。

## 8. 脚本调用
- `python3 -B scripts/main.py --help`：查看参数与默认路径。
- 主流程：`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。
- 定向查询：把查询写入 `/app/work/queries.json` 后重跑同一命令，结果进入 `query_log` 与 `answers`。
- 自检：`python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work`（对产物做格式与一致性检查，退出码 0/非 0）。
- 脚本只依赖标准库；路径用相对 `scripts/...`（在 skill 根目录执行；若 Harness 工作目录不同，先 `cd /skills/weekly-skill`）。

## 9. 边界
- 一个 Skill 只解决「只读科研数据快照的查询与分析」这一类任务，不覆盖写库、ETL 调度或大图渲染。
- 数据字典、任务书与数据快照是事实依据；不依据领域标签补造数据，不携带预写答案或正式测试产物。
- 官方 3 个固定案例与白板由平台提供；本 Skill 仅提供可复用的字段语义先验与通用流程，不预填任何 case 结果。

## 交付结构
`SKILL.md`；`scripts/{main,core_impl,selftest}.py`；`references/{checklist,sql-patterns,dataset-fields}.md`。
