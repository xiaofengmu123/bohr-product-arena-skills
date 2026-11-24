---
name: sci-data-analysis
description: 科研数据多步骤分析 Skill：导入→字段核对→清洗→统计检验→可视化→报告，输出契约逐字段对齐 output_contract 的 result.json 与可复核、可回算的产物。当任务要求对 CSV/TSV/JSON/SQLite 科研数据完成缺失/异常/重复处理、组间比较、相关/回归分析、作图并生成可回算报告时使用。
version: 1.1.0
---

# 科研数据多步骤分析 Skill

## 1. 角色与何时使用

本 Skill 把"一张原始表 + 一句需求"编排成六段流水线：**导入 → 字段核对 → 清洗 → 统计 → 可视化 → 报告**，并额外强化**输出契约对齐**：`result.json` 的字段逐一对应 `/input/case.json` 的 `output_contract`，未知键原样保留、不改名、不删除，缺值给 `null`/`[]` 并写入 `change_log`。目标是稳定产出**有证据链、可重跑、失败可解释**的结果，而非只剩结论和截图。

适用：CSV/TSV/JSON/SQLite 表格数据；缺失值、异常值、重复记录处理；分组比较与假设校验；相关与单变量 OLS 回归；图表选型；报告数字回填。不适用：纯聊天问答、无数据任务、需外部数据库或网络的一键脚本。

## 2. 输入契约（先发现，再假设）

- 运行时原始输入位于 `/input/`，至少含 `case.json`（`case_id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料（CSV/TSV/JSON/SQLite）按约定同目录存放。
- 工作目录 `/app/work`（中间产物）；最终产物写入 `/app/output/`。
- 脚本位于 Skill 根 `scripts/` 下（仅标准库）。**先在 Skill 根目录 `cd /skills/weekly-skill`**，再以相对路径调用 `python3 -B scripts/main.py ...`；`--input/--output/--work` 缺省即 `/input`、`/app/output`、`/app/work`。
- 不假设固定文件名/键名：扫描输入目录发现数据源；`case.json` 缺 `output_contract` 时用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录。
- 文本按 `utf-8-sig → utf-8 → gbk → latin-1` 依次尝试；坏编码/坏 JSON 降级不崩溃；单文件超过 20MB 跳过并记录。

## 3. 输出契约（本版强化方向：逐字段对齐）

必须产出 `/app/output/result.json`，顶层含：

- `case_id`：取自 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注；
- **`output_contract` 的全部字段原样回填在顶层**：不改名、不删未知键、缺值给 `null`/`[]`；每一个字段的填充动作（`filled` 或 `defaulted to null/[]`）都写入 `change_log`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值；
- `sources`：每项含 `locator` + 逐字 `quote` + `role`，覆盖主表、task.md、case.json；
- `uncertainty`（口径/近似/未决项）、`pending_human`（需人工确认项）、`change_log`（逐步变更）、`errors`、`degradations`、`runtime_seconds`、`skill_version`。

主产物（全部落在 `/app/output/`，可编辑、可复核）：`report.md`（正文数字只回填自 `stats.json`）、可编辑 `figure_*.svg`、清洗表 `cleaned.csv`、规则表 `clean_meta.json`、统计 `stats.json`、字段字典 `profile.json`。环境另有 matplotlib/pandas/scipy 时，Agent 可补 PNG/PDF 预览，但不得丢失 SVG 与重算依据。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须先落盘一版 `status=partial` 的结果骨架，之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。全部脚本幂等，可安全重跑。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work` 写 `result.json` 骨架（status=partial，契约键全为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `python3 -B scripts/main.py profile ...`：发现数据源、字段字典、类型/行数/缺失/重复初检 → `work/profile.json`；读 task.md 确定主表/分组列/目标列 |
| 2 分析 | 6–10 分钟，≤25 请求 | `python3 -B scripts/main.py all ...`：clean（去重、缺失，**只记录规则与行数影响，不静默丢数据**）→ analyze（描述统计 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson / 单变量 OLS，附前提与近似说明）→ render（SVG + report.md）→ finalize（回填全部契约字段） |
| 3 核对 | 10–13 分钟，≤20 请求 | 核对报告数字与 `stats.json` 同源；补异常说明与 uncertainty；`finalize` 复核契约字段是否逐一对齐 |
| 4 自检 | 13–15 分钟，≤7 请求 | `python3 -B scripts/selftest.py ...` 重跑验证：JSON 可解析、SVG 可解析、契约键齐全、行数守恒；修复后最终落盘；预留约 5 次请求应对重试 |

## 5. 契约对齐规则（本版设计取舍）

1. 先递归查找 `case.json` 中的 `output_contract`（dict 或 list 均可），无则用默认三键。
2. 对每个契约字段做语义映射：`analysis_results/statistics` → 完整 `stats.json`；`quality_evidence/reproducibility` → `clean_meta.json`；`anomaly_notes` → 异常说明列表；`report` → 报告相对路径；`figures/charts/visualizations` → 图文件列表；`cleaned_table/csv` → 清洗表路径。
3. 未命中语义映射的字段**原样保留键名**，值给 `null`（标量）或 `[]`（spec 含 list/array 提示），并写入 `degradations` + `change_log` + `pending_human`，绝不静默改名或删除。
4. 契约字段全部顶层回填，另存 `_output_contract_source` 原文以便审计；`change_log` 对每个字段记录 `filled/action/spec`。
5. 契约完整性是硬指标：自检脚本会逐键比对 result.json 是否覆盖 output_contract 全部键。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + output_contract 全部字段（顶层）+ `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 的删行总数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：两组 Welch t / Mann-Whitney，三组及以上 ANOVA / Kruskal-Wallis；p 值、效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的均值/分位/误差来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 每个契约字段在 `change_log` 中有 `filled/action` 记录，未命中字段显式标 defaulted。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误/SQLite 失败均退出码 0 并在 errors/degradations 说明。
- [ ] 确定性：同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机数、无时间戳）。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 7. 降级策略（诚实标注 limitations，不编造）

- 缺 `case.json` → status=input_error；有 case 但无数据文件 → insufficient_evidence；两者都在 result.json 写清。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 使用纯标准库 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- 无法解释的结果/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。
- 任何未捕获异常 → 兜底写最小合法 `result.json`（status=input_error + errors），退出码 0，不留空输出。

## 8. 边界

一个 Skill 只解决"表格数据多步分析"这一类任务；没有真实材料时输出"材料不足"，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。脚本只依赖 Python 标准库，可部署到不含第三方包的环境。
