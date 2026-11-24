---
name: sci-data-analysis
description: 科研数据多步骤分析 Skill：导入→字段核对→清洗→统计检验→可视化→报告，全程显式记录 limitations/uncertainty 与逐字证据溯源，输出可复核的 result.json 与可编辑 SVG/report.md。适用于 CSV/TSV/JSON/SQLite 表格数据的缺失/异常/重复处理、组间比较、相关分析与作图报告任务；缺数据或缺软件时如实标注，不编造。
version: 2.0.0
---

# 科研数据多步骤分析 Skill

## 1. 角色与何时使用

本 Skill 把“原始表 + 一句需求”的科研数据分析任务编排成可复核流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化 → 报告**。它保证 Agent 稳定产出**有证据链、可重跑、失败可解释、缺什么就写什么**的结果，而不是只剩结论和截图。

适用：CSV/TSV/JSON/SQLite 表格数据；缺失值、异常值、重复记录处理；分组比较与假设校验；相关分析；图表选型；报告数字回填；数据库字段核查。不适用：纯聊天问答、无数据任务、需外部网络/未授权数据库的一键脚本。

本版（v2）的设计取舍是**降级与不确定性显式化**：`limitations` 与 `uncertainty` 是一等公民，任何近似、缺软件、缺数据、口径选择都被写进 `result.json`，绝不静默补造。

## 2. 输入契约

- 运行时原始输入位于 `/input/`，至少含 `case.json`（任务元信息、`case_id`/`challenge_id`/`armiId`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料按约定放在 `/input` 下（CSV/TSV/JSON/SQLite 等）。
- 中间产物写 `/app/work`，最终产物写 `/app/output/`。
- 先“发现再假设”：扫描输入目录，不假设固定文件名/键名/列名；主表、分组列、目标列先自动探测，再允许 `--primary/--group/--targets` 覆盖。
- `case.json` 缺 `output_contract` 时，用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录；`case.json` 本身缺失或坏 JSON 也要记录，不崩溃。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 `case.json` 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并在 `degradations` 标注；
- **题目 `output_contract` 的全部字段**：原样回填为顶层键，不改名、不删未知键；缺值给 `null`/`[]`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值（无数据=`insufficient_evidence`、输入目录不可读且无数据=`input_error`、有降级/待确认=`partial`）；
- `sources`（每项含 `locator` + 逐字 `quote`）、`uncertainty`、`limitations`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`，可回算）+ 可编辑 SVG 图（`figure_*.svg`）。环境有 matplotlib/pandas/scipy 时可补 PNG/PDF 预览，但不得丢失 SVG 与重算依据。清洗表 `work/cleaned.csv`、规则表 `work/clean_meta.json`、统计 `work/stats.json`、字段字典 `work/profile.json` 一并保留。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。脚本全部幂等、确定性（同输入两次运行 `stats.json` 与 SVG 字节一致）。

以下命令在 Skill 根目录执行；若 Harness 的 cwd 不是 Skill 根目录，先把 `scripts/` 前缀替换为 Skill 绝对路径。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 `result.json` 骨架（status=partial，全部契约键为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `discover`：扫描数据源、按 CSV/TSV/JSON/SQLite 载入 → `work/discovery.json`；`profile`：字段字典、类型/行数/缺失/重复/异常初检 → `work/profile.json`；读 task.md 确定主表/分组列/目标列，必要时 `--primary/--group/--targets` 覆盖 |
| 2 分析 | 6–10 分钟，≤25 请求 | `clean`（去重、缺失计数、异常标记，**只记录规则与行数影响，不静默丢数据**）→ `analyze`（描述统计 + Jarque-Bera 正态性 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Levene / Pearson / Spearman，附前提与近似说明）→ `render`（SVG + report.md） |
| 3 核对 | 10–13 分钟，≤20 请求 | 核对报告数字与 `stats.json` 同源；补异常说明、`uncertainty`、`limitations`；`finalize` 定 status 并回填全部契约字段 |
| 4 自检 | 13–15 分钟，≤7 请求 | `python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work`：JSON 可解析、SVG 可解析、契约键齐全、清洗行数一致；修复后最终落盘；预留约 5 次请求应对重试 |

一键跑全流程：`python3 -B scripts/main.py run --input /input --output /app/output --work /app/work`；也可分阶段调用 `scaffold/discover/profile/clean/analyze/render/finalize`。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + 题目 output_contract 全部字段 + `status/sources/uncertainty/limitations/pending_human/change_log`。
- [ ] `status` 诚实：无数据=`insufficient_evidence`、输入目录不可读=`input_error`、有降级或待确认=`partial`。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 的删行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：检验方法匹配组数与方差前提；报告 p 值/效应量与 `stats.json` 一致；p=0 写作 `<1e-300`。
- [ ] 图表与数据同源：SVG 的分位/误差来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0，并在 `errors/degradations` 说明。
- [ ] 确定性：同一输入两次运行，SVG 与 `stats.json` 字节一致（无随机数）。
- [ ] 不编造：缺软件/缺数据 → `limitations`/`pending_human`/`not_reported`，绝不虚构数值、DOI、结论。

## 6. 降级策略（诚实标注，不编造）

- 缺 `case.json` 但有数据 → status=partial，记入 `degradations`，case_id 用目录名；两者都缺 → `insufficient_evidence`；输入目录不可读 → `input_error`。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功则记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 用本 Skill 纯标准库 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- xlsx/xls 需 openpyxl，无则记 `limitations` 并跳过；SQLite 连接失败/表缺失记 `errors`。
- 口径争议/无法解释 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 7. 边界

一个 Skill 只解决“表格数据多步分析”这一类任务；没有真实材料时输出“材料不足”，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
