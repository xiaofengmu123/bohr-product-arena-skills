---
name: sci-data-analysis
description: 科研数据多步骤分析 Skill（确定性版）：导入→字段核对→清洗→统计检验→可视化→报告，输出可复核的 result.json、可回算的 report.md 与可编辑 SVG；内置 scripts/selftest.py 自检与逐条可勾选清单，保证同一输入两次运行产物字节一致。当任务要求对 CSV/TSV/JSON/SQLite 科研数据完成缺失/异常/重复处理、组间比较、相关分析、作图并生成可回算报告时使用。
version: 1.0.0
---

# 科研数据多步骤分析 Skill（确定性版）

## 1. 角色与何时使用

本 Skill 把“原始表 + 一句需求”编排成六段流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化 → 报告**。它与参考实现的核心差异是**确定性优先 + 自检闭环**：产物不依赖随机数与系统时钟，脚本附带可运行的自检电池，任何一步失败都不留下空 `/app/output`。

适用：CSV/TSV/JSON/SQLite 表格；缺失值、异常值、重复记录；分组比较与假设校验；相关分析；图表选型；报告数字回填。不适用：纯聊天问答、无数据任务、需外部数据库/网络的一次性脚本。

## 2. 输入契约

- 运行时原始输入位于 `/input/`，至少含 `case.json`（任务元信息、`case_id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料（CSV/TSV/JSON/SQLite）同目录存放。
- 工作目录 `/app/work`（中间产物），最终产物写入 `/app/output/`（含 `result.json`）。
- 脚本入口 `scripts/main.py`（仅标准库），实现位于 `scripts/core_impl.py`；自检入口 `scripts/selftest.py`。三者同目录，用相对路径调用。
- **先发现再假设**：扫描输入目录，不假设固定文件名/键名/表名。`case.json` 缺 `output_contract` 时使用默认三键 `analysis_results / quality_evidence / anomaly_notes`，并在 `degradations` 记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 case.json 的 `case_id/challenge_id/armiId/id`，缺失时回退为输入目录名并在 `degradations` 标注；
- **题目 `output_contract` 的全部字段**：原样回填、不改名、不删未知键；缺值给 `null`/`[]`；未知语义键给 `null` 并记入 `degradations`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值；
- `sources`（每项含 `locator` + 逐字 `quote`）、`uncertainty`、`pending_human`、`change_log`（逐步变更，无时间戳）、`errors`、`degradations`、`limitations`、`runtime`、`fingerprint` 与 `artifacts`（各产物 sha256）。

主产物：`/app/output/report.md`（正文数字全部由 `work/stats.json` 程序化生成，可回算）+ 可编辑 `figure_*.svg`（条形图、箱线图、散点图）。环境另有 matplotlib/pandas/scipy 时可补 PNG/PDF 预览，但不得丢失 SVG 与重算依据。清洗表 `work/cleaned.csv`、规则表 `work/clean_meta.json`、统计 `work/stats.json`、字段字典 `work/profile.json` 一并保留。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须落盘一版 partial 结果骨架，之后只做增量改进**。任何一步失败都不允许留下空 `/app/output`。全部脚本幂等，可安全重跑；产物字节确定。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 `result.json` 骨架（status=partial，全部契约键为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `profile`：发现数据源（含 SQLite 表）、字段字典、类型/行数/缺失/重复/异常初检 → `work/profile.json`；读 task.md 确定主表/分组列/目标列，必要时 `--group/--targets/--intent` 覆盖 |
| 2 分析 | 6–10 分钟，≤25 请求 | `clean`（去重、缺失、类型强制、异常只标记不删，记录规则与行数影响）→ `analyze`（描述统计 + Welch t / Student t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson / Spearman，自动校验正态性与方差齐性选择方法，附前提与近似说明）→ `render`（SVG + report.md） |
| 3 核对 | 10–13 分钟，≤20 请求 | 核对报告数字与 `stats.json` 同源；补异常说明与 uncertainty；`finalize` 定 status 并回填全部契约字段、写入 manifest 与 fingerprint |
| 4 自检 | 13–15 分钟，≤7 请求 | 运行 `python3 -B scripts/selftest.py` 与 `python3 -B scripts/main.py check`；修复后最终落盘；预留约 5 次请求应对重试 |

一步到底的替代路径：`python3 -B scripts/main.py run --input /input --output /app/output --work /app/work [--group G --targets A,B --intent group_comparison]`，内部仍先落骨架再增量推进。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + 题目 output_contract 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=insufficient_evidence、case.json 坏且无数据=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 的 `consistency_check` 为 true（删行数 == 原始行数 − `cleaned.csv` 行数）；异常只标记不删。
- [ ] 统计口径正确：先校验正态性（D'Agostino K²）与方差齐性（Levene 中位数中心），再按组数与前提选检验；报告 p 值/效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的均值/误差/分位来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误/SQLite 失败均退出码 0，并在 errors/degradations 说明。
- [ ] 确定性：`python3 -B scripts/selftest.py` 全绿；同一输入两次运行，除 `result.json` 的 `runtime` 时间戳外所有产物字节一致，`fingerprint` 相同。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 6. 降级策略（诚实标注，不编造）

- 空输入目录 → `insufficient_evidence`，退出码 0；case.json 存在但不可解析且无数据 → `input_error`；case.json 坏但有数据 → 记 `degradations` 并继续分析。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足（n<8 的正态性、n<2 的检验）→ 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/gbk/latin-1，降级成功则记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 本 Skill 纯标准库实现全部统计（t/F/χ² 经不完全 beta/gamma、正态 CDF 经 erf）与 SVG 渲染，核心交付不受影响。
- 无法解释的结果/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 7. 确定性设计（本版侧重）

- 全程无随机数、无随机种子需求；`json.dumps(sort_keys=True, separators=(",",":"))`，浮点统一 12 位有效数字规范化，非有限值置 `null`。
- `runtime` 是 result.json 中唯一墙钟字段，自检时剥离后再比对；其余字段与所有中间/最终产物字节级可复现。
- 每个产物在 `result.json.artifacts` 中登记 sha256；`fingerprint` = 脚本版本 + case_id + 契约键 + 输入文件 sha256 的哈希，供跨机复算核对。
- 分箱/排序/坐标轴刻度均确定性计算，SVG 两次渲染字节一致。

## 8. 边界

一个 Skill 只解决“表格数据多步分析”这一类任务；没有真实材料时输出“材料不足”，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。`references/` 下为自检清单与统计方法说明，供人工复核与复用。
