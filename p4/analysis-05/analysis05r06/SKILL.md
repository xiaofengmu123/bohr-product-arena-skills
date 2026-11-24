---
name: sci-data-analysis
description: 科研数据多步骤分析 Skill：导入→字段核对→清洗→统计检验→可视化→报告，全程以结构化中间表示（profile/plan/stats/figure_model）串联，输出可复核的 result.json、可回算的 report.md 与可编辑 SVG。当任务要求对 CSV/TSV/JSON/SQLite 科研数据完成缺失/异常/重复处理、组间比较、相关分析、作图并生成可回算报告时使用。
version: 1.1.0
---

# 科研数据多步骤分析 Skill

## 1. 角色与何时使用

本 Skill 把“原始表 + 一句需求”编排成可复核的七段流水线：**导入 → 字段核对 → 计划 → 清洗 → 统计 → 可视化 → 报告**。与只留结论和截图的常见做法不同，它把每一段的输入、决策与输出都落成**结构化中间表示**（`profile.json`、`plan.json`、`clean_meta.json`、`stats.json`、`figure_model.json`），形成一条可逐环回算的证据链。

适用：CSV/TSV/JSON/SQLite 表格数据；缺失值、异常值、重复记录处理；分组比较与假设校验；相关分析；图表选型；报告数字回填。不适用：纯聊天问答、无数据的任务、需外部数据库或联网的一键脚本。

## 2. 输入契约

- 运行时原始输入在 `/input/`，至少含 `case.json`（任务元信息、`case_id/challenge_id/armiId/id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料也按约定放在 `/input` 下（CSV/TSV/JSON/SQLite 等）。
- 中间产物写入 `/app/work`，最终产物写入 `/app/output/`。
- 脚本统一用相对路径 `scripts/main.py`（仅标准库）调用；`scripts/core_impl.py`、`scripts/selftest.py` 必须同目录存在。
- **先发现再假设**：扫描输入目录，不假设固定文件名/键名；`case.json` 缺 `output_contract` 时用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下：

- `case_id`：取自 case.json 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注。
- `contract`：题目 `output_contract` 的**全部字段原样回填**，不改名、不删未知键；缺值给 `null`/`[]`。
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值。
- `sources`（每项含 `locator` + 逐字 `quote`）、`uncertainty`、`pending_human`、`change_log`（逐步变更）、`errors`、`degradations`、`runtime`、`artifacts`。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`，可回算）+ 可编辑 `figure_1.svg`。中间表示一并保留：`work/profile.json`（字段字典）、`work/plan.json`（执行计划）、`work/clean_meta.json`（清洗规则）、`work/cleaned.csv`（清洗表）、`work/stats.json`（统计）、`work/figure_model.json`（图形模型）、`work/manifest.json`（产物哈希清单）。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须落盘一版 `result.json` partial 骨架，之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。全部脚本幂等，可安全重跑。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 partial 骨架（status=partial，全部契约键为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `profile`：发现数据源、字段字典、类型/行数、缺失/重复/异常初检 → `work/profile.json` |
| 2 计划 | 6–8 分钟，≤8 请求 | `plan`：读 profile 与 task.md 确定主表/分组列/目标列，可 `--primary/--group/--targets` 覆盖 → `work/plan.json` |
| 3 分析 | 8–12 分钟，≤25 请求 | `clean`（去重、缺失计数、异常只标记，**只记录规则与行数影响，不静默丢数据**）→ `analyze`（描述统计 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson，附前提与近似说明）→ `figure`（`figure_model.json` + `figure_1.svg`）→ `report`（report.md） |
| 4 核对 | 12–14 分钟，≤14 请求 | 核对报告数字与 `stats.json` 同源；补异常说明与 uncertainty；`finalize` 定 status 并回填全部契约字段、写 `manifest.json` |
| 5 自检 | 14–15 分钟，≤5 请求 | `python3 -B scripts/selftest.py` 重跑验证、JSON 可解析、SVG 可解析、契约键齐全、确定性一致；修复后最终落盘 |

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + `contract`（题目 output_contract 全部字段）+ `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 的去重行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：检验方法匹配组数与方差前提；报告 p 值/效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：`figure_1.svg` 只由 `figure_model.json` 渲染，其数值来自 `stats.json`；轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0 并在 errors/degradations 说明。
- [ ] 确定性：同一输入两次运行，`stats.json` 字节一致（无随机数，JSON 全部 sort_keys）。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 6. 降级策略（诚实标注，不编造）

- 缺 `case.json` → status=input_error；有 case 但无数据文件 → insufficient_evidence；两者都写入 result.json。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/gbk/latin-1，降级成功则记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 使用本 Skill 纯标准库的 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- 无法解释的结果/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。
- 本 Skill 不预置正式测试案例、白板或分数，不伪造运行结果。

## 7. 边界

一个 Skill 只解决“表格数据多步分析”这一类任务；没有真实材料时输出“材料不足”，不用示例填充。图表数值以 `figure_model.json` 为唯一中间表示来源，报告数字以 `stats.json` 为唯一来源，二者不一致时以 `stats.json` 为准并记录冲突。
