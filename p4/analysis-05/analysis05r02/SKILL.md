---
name: sci-tabular-analysis
description: 科研表格多步分析 Skill（预算时钟版）：导入→字段核对→清洗→统计检验→可视化→报告，内置 900 秒/64 请求阶段预算与“3 分钟内先落盘 partial 骨架”的时钟机制，产出可复核、可回算、失败可解释的 result.json 与可编辑 SVG/报告。当任务要求对 CSV/TSV/JSON/SQLite 科研数据完成缺失/异常/重复处理、组间比较、相关分析、作图并生成可回算报告时使用。
version: 2.0.0
---

# 科研表格多步分析 Skill（预算时钟版）

## 1. 角色与何时使用

把“原始表 + 一句需求”编排成六段流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化 → 报告**，并让每一步都有**证据链、可重跑、失败可解释、时间可观测**。本版的差异化设计是**预算时钟与早落盘**：脚本维护 `work/clock.json`，按墙钟给出阶段剩余预算与行动建议；`scaffold` 在最初 3 分钟内写出一版**合法且完整**的 partial `result.json`，之后所有命令只做幂等增量合并，绝不推倒重写已定结果。

适用：CSV/TSV/JSON/JSONL/SQLite 表格数据；缺失、异常、重复处理；分组比较与假设校验；相关分析；图表选型；报告数字回填。不适用：纯聊天问答、无数据任务、需外部网络/私有数据库的一键脚本。

## 2. 输入契约

- 运行时原始输入位于 `/input/`：至少含 `case.json`（`case_id/challenge_id/armiId/id`、可能的 `output_contract`）与 `task.md`；数据材料（CSV/TSV/JSON/JSONL/SQLite）按约定放 `/input` 下。**先发现再假设**：扫描目录，不假设固定文件名/键名。
- 中间产物写 `/app/work/`，最终产物写 `/app/output/`（必须含 `result.json`）。
- 脚本从 Skill 根目录用相对路径调用：`python3 -B scripts/main.py <command> ...`。仅标准库。
- `case.json` 缺 `output_contract` 时使用默认三键 `analysis_results / quality_evidence / anomaly_notes`，并在 `degradations` 注明。

## 3. 输出契约

必须产出 `/app/output/result.json`，结构如下：

- `case_id`：取自 `case.json` 的 `case_id/challenge_id/armiId/id`，缺失用输入目录名并标注；
- **题目 `output_contract` 的全部字段**：原样回填、不改名、不删未知键；缺值给 `null`/`[]`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成度赋值；
- `sources`：每项含 `locator`（文件/表/行列定位）+ `quote`（逐字原文）+ `provenance`；
- `uncertainty`（口径/近似/未决项）、`pending_human`、`errors`、`degradations`、`change_log`（逐步变更记录）、`runtime`（秒数与请求估计）、`artifacts`（主产物路径清单）。

主产物：`/app/output/report.md`（正文数字全部由 `work/stats.json` 回填，无手写数字）+ 可编辑 SVG（`figure_*.svg`）。环境另有 pandas/scipy/matplotlib 时可补 PNG/PDF 预览，但不得丢失 SVG 与重算依据。中间产物：`work/cleaned.csv`、`work/clean_meta.json`、`work/stats.json`、`work/profile.json`、`work/clock.json`。

## 4. 执行流程与预算（总 900 秒 / 64 次模型请求）

核心纪律：**阶段 0 的前 3 分钟内必须 `scaffold` 落盘 partial 骨架**；任何一步失败都不允许留下空 `/app/output`；所有脚本幂等、确定（无随机数）。

| 阶段 | 累计时间预算 | 请求预算 | 步骤（输入 → 输出） |
|---|---|---|---|
| 0 骨架 | 0–3 分钟 | ≤4 | 读 `/input/case.json`+`task.md` → 提取 `case_id`、`output_contract`、任务要求；`python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work` 先写合法 `result.json`（status=partial，全部契约键为 null/[]，`change_log` 记一条 `scaffold`）。 |
| 1 探查 | 3–6 分钟 | ≤8 | `profile`：发现数据源、字段字典、类型/行数、缺失/重复/异常初检 → `work/profile.json`；读 task.md 定主表/分组列/目标列，可用 `--primary/--table/--group/--targets` 覆盖。 |
| 2 清洗+分析 | 6–10 分钟 | ≤25 | `clean`（去重、缺失、异常，**只记录规则与行数影响，不静默丢数据；异常只标记不删**）→ `analyze`（描述统计 + Welch t / ANOVA F / Mann-Whitney U / Kruskal-Wallis H / Pearson，附前提与近似说明）。 |
| 3 报告+核对 | 10–13 分钟 | ≤20 | `render`（SVG + report.md，数字取自 `stats.json`）→ 核对正文数字与 `stats.json` 同源 → 补 `uncertainty`/`sources` 逐字 quote → `finalize` 回填全部契约字段。 |
| 4 自检 | 13–15 分钟 | ≤7 | 重跑 `scripts/selftest.py`：JSON/SVG 可解析、契约键齐全、`clean_meta` 行数等式成立、报告数字与 `stats.json` 同源、两次运行字节一致；修复后最终落盘。 |

**预算时钟**：`scaffold` 写 `work/clock.json`（`t0`、`deadline_s=900`、各阶段截止秒）。之后每条命令 `touch_clock` 更新 `elapsed_s/remaining_s` 与 `recommendation`：`remaining < 120s` 时建议跳过非必需图、直接 `finalize`；`remaining < 30s` 时建议停止新增、只修 `result.json`。Agent 每阶段开始前 `python3 -B scripts/main.py clock --work /app/work` 读一次，按建议取舍。

**增量合并**：`scaffold` 之后 `finalize` 只回填契约键、`status`、`sources`、`uncertainty`、`change_log`；已存在的非空值不被覆盖，避免把早落盘的稳定结果改坏。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果（`change_log` 可证）。
- [ ] `result.json` 含 `case_id` + 题目 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 删行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：2 组用 Welch t + Mann-Whitney，≥3 组用 ANOVA F + Kruskal-Wallis，报告 p 值与效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的均值/误差/分位来自 `stats.json`，轴标签注明单位与误差含义（SEM/SD/IQR）。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0 并在 `errors/degradations` 说明。
- [ ] 确定性：同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机数）。
- [ ] 不编造：缺失软件/数据 → `pending_human`/`not_reported`，绝不虚构数值、DOI、结论。
- [ ] 证据溯源：每个关键结论给 `locator` + 逐字 `quote`；同一指标两个来源不一致时两个都记并标 `source_conflict`。

## 6. 降级策略（如实标注 limitations，不编造）

- 缺 `case.json` → status=input_error；有 case 但无数据文件 → insufficient_evidence；两者都在 `result.json` 写清缺口。
- 某列类型不符/全空 → 记 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功记 `degradations`。
- SQLite 打开失败/表缺失 → `errors`+`degradations` 记录，继续其它可用文件。
- 环境缺 pandas/scipy/matplotlib → 用本 Skill 纯标准库的 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- 无法解释的结果/口径争议 → 写 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 7. 边界

一个 Skill 只解决“表格数据多步分析”这一类任务；没有真实材料时输出“材料不足”，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
