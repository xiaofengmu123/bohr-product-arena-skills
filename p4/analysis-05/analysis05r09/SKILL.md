---
name: sci-data-analysis
description: 科研表格数据多步骤分析 Skill：导入→字段核对→清洗→统计检验→可视化→报告，产出可回算的 result.json 与可编辑 SVG/report.md。当任务要求对 CSV/TSV/JSON/SQLite 数据做缺失/异常/重复处理、组间比较、相关分析、作图并生成可复核报告时使用。
version: 1.1.0
---

# 科研表格数据多步分析

## 1. 角色与适用

本 Skill 把“原始表 + 一句需求”编排成可复核的五段流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化/报告**，稳定产出**有证据链、可重跑、失败可解释**的交付，而非只剩结论与截图。

适用：CSV/TSV/JSON/SQLite 表格；缺失/异常/重复处理；分组比较与相关分析；图表选型；报告数字回填。
不适用：无数据的闲聊、需外部网络或专有数据库的操作。

## 2. 输入契约

- 运行时原始输入在 `/input/`，至少含 `case.json`（元信息、`case_id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据文件同目录或子目录，扩展名 `.csv/.tsv/.json/.db/.sqlite/.sqlite3/.txt`。
- 工作目录 `/app/work`（中间产物），最终产物写入 `/app/output/`。
- 脚本只用标准库；入口 `scripts/main.py`，实现 `scripts/core_impl.py`，自检 `scripts/selftest.py`。
- **先发现再假设**：扫描输入目录，不假设固定文件名/键名；`case.json` 缺 `output_contract` 时用默认三键 `analysis_results / quality_evidence / anomaly_notes`，并记入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：

- `case_id`：取 `case.json` 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注；
- **题目 `output_contract` 的全部字段**：原样回填、不改名、不删未知键；缺值给 `null`/`[]`；
- `status`（`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值）、`sources`（每项含 `locator + quote` 的证据定位）、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`artifacts`。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`）+ 可编辑 SVG（`figure_*.svg`）。中间产物保留：`work/profile.json`（字段字典）、`work/clean_meta.json`（规则）、`work/cleaned.csv`、`work/stats.json`。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须落盘一版 `result.json` 骨架，之后只做增量改进**；任何一步失败都不得留下空 `/app/output`。脚本幂等，可安全重跑。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `case.json`+`task.md`，提取 `case_id`、`output_contract`、需求；立即执行 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 `result.json`（status=partial，契约键全为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `python3 -B scripts/main.py profile ...` 发现数据源、字段字典、类型/行数/缺失/重复初检；读 `task.md` 确定主表/分组列/目标列，必要时 `--primary/--group/--targets` 覆盖 |
| 2 分析 | 6–10 分钟，≤25 请求 | `analyze`：去重、缺失记账、异常标记（只记录规则与行数影响，不静默丢数据）→ 描述统计 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson，附前提与近似说明 |
| 3 出图报告 | 10–12 分钟，≤20 请求 | `render`：生成 SVG + `report.md`；核对报告数字与 `stats.json` 同源，补异常与 uncertainty |
| 4 定稿自检 | 12–15 分钟，≤7 请求 | `python3 -B scripts/selftest.py ...` 自检后 `finalize` 定 status、回填全部契约字段；预留约 5 次请求重试 |

一步跑全链路（在 Skill 根目录 `/skills/weekly-skill` 执行）：`python3 -B scripts/main.py run --input /input --output /app/output --work /app/work`。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + 题目 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 删行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：两分组用 Welch t + Mann-Whitney，多分组用 ANOVA + Kruskal-Wallis；报告 p 值/效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的均值/误差来自 `stats.json`；坐标轴注明单位与误差含义，非零基线明确标注。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0，并在 `errors/degradations` 说明。
- [ ] 确定性：同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机数）。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 6. 降级策略（如实标注 limitations，不编造）

- 缺 `case.json` → status=input_error；有 case 但无数据文件 → insufficient_evidence；均写入 `errors`。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功记 `degradations`。
- 无 pandas/numpy/scipy/matplotlib → 本 Skill 纯标准库实现统计与 SVG，核心交付不受影响。
- 口径争议/无法解释 → 写 `uncertainty` 与 `pending_human` 请求人工确认，不擅自定论。

## 7. 证据溯源与边界

- 每个关键结论给 `locator + quote`：如 `case.json` 的 case_id、`task.md` 首行需求、数据文件的行列形状。
- 冲突信息不隐藏：同一指标两个来源不一致时都记录并标 `source_conflict`。
- 一个 Skill 只解决“表格多步分析”一类任务；无真实材料时输出“材料不足”，不用示例填充。
- 正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
