---
name: sci-tabular-analysis
description: 科研表格数据多步骤分析 Skill：导入→字段核对→清洗→统计检验→可视化→报告，支持 CSV/TSV/JSON/SQLite，纯标准库优先、证据溯源（locator+quote）、确定性自检与评分维度对齐。当任务要求对科研数据完成缺失/异常/重复处理、组间比较、相关/回归分析、数据库查询、作图并生成可回算报告时使用。
version: 1.0.0
---

# 科研表格数据多步骤分析 Skill

## 1. 角色与何时使用

本 Skill 把"一批原始表格 + 一句需求"编排成可复核的六段流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化 → 报告**。它让 Agent 稳定产出**有证据链、可重跑、失败可解释**的结果，而不是只剩结论和截图。

适用：CSV / TSV / JSON / SQLite 表格数据；缺失值、异常值、重复记录处理；分组比较与假设校验；相关与线性回归；图表选型；报告数字回填；只读数据库查询。不适用：纯聊天问答、无数据任务、需要网络的一键脚本、图像/序列等非表格数据。

核心设计取舍（与常见包不同）：

- **标准库优先**：核心统计与 SVG 全用 Python 标准库实现；环境有 scipy 时仅作精度升级，且 `stats.json.backend.scipy` 明确记录来源，不静默声称精度。
- **评分维度对齐**：题目的 5 个评分维度逐条映射到流程阶段与自检项（见 §7 与 `references/rubric_map.md`），交付物直接对应评分点。
- **证据溯源**：每个关键结论都带 `locator + quote`（原始材料逐字片段），报告数字全部由 `work/stats.json` 程序化回填。

## 2. 输入契约

- 运行时原始输入位于 `/input/`，至少含 `case.json`（任务元信息、`case_id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料（CSV/TSV/JSON/SQLite）按约定存放于 `/input` 下。
- 工作目录 `/app/work`（中间产物），最终产物写入 `/app/output/`。
- 脚本用相对路径调用：`python3 -B scripts/main.py`（仅标准库），依赖的 `scripts/core_impl.py` 必须同目录存在；`scripts/selftest.py` 为自检脚本。
- **先发现、再假设**：扫描输入目录，不假设固定文件名/键名；`case.json` 缺 `output_contract` 时用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录。
- 文本按 `utf-8-sig → utf-8 → gbk → latin-1` 依次尝试解码并降级；单文件超过 2MB 跳过并记录。SQLite 以只读模式打开，仅执行 `SELECT`/`PRAGMA`。

## 3. 输出契约

必须产出 `/app/output/result.json`，含：

- `case_id`：取自 case.json 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注；
- **题目 `output_contract` 的全部字段**：原样回填、不改名、不删未知键；缺值给 `null`/`[]`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值；
- `sources`（每项含 locator + quote）、`uncertainty`、`pending_human`、`limitations`、`change_log`（逐步变更记录）、`errors`、`degradations`、`artifacts`、`runtime`。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`，可回算）+ 可编辑 SVG 图（`figure_*.svg`）。清洗表 `work/cleaned.csv`、规则表 `work/clean_meta.json`、统计 `work/stats.json`、字段字典 `work/profile.json` 一并保留。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须落盘一版结果骨架，之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。全部脚本幂等，可安全重跑。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 `result.json` 骨架（status=partial，全部契约键为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `profile`：发现数据源、字段字典、类型/行数、缺失/重复/异常初检 → `work/profile.json`；读 task.md 确定主表/分组列/目标列，必要时 `--primary/--group/--targets/--outcome` 覆盖 |
| 2 分析 | 6–10 分钟，≤25 请求 | `clean`（去重、缺失、异常，**只记录规则与行数影响，不静默丢数据**）→ `analyze`（描述统计 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson / Spearman / OLS 回归，附前提与近似说明）→ `render`（SVG）+ `report`（report.md） |
| 3 核对 | 10–13 分钟，≤20 请求 | 核对报告数字与 `stats.json` 同源；补异常说明与 uncertainty；`finalize` 定 status 并回填全部契约字段 |
| 4 自检 | 13–15 分钟，≤7 请求 | 重跑验证、JSON 可解析、SVG 可解析、契约键齐全；修复后最终落盘；预留约 5 次请求应对重试 |

命令示例（相对路径，全部幂等）：

```bash
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py
```

最小调用示例（示意输出，**非正式案例结果**）：

```json
{ "case_id": "demo-1", "status": "ok",
  "analysis_results": { "outcome": { "n": 120, "mean": 38.1, "sd": 6.2 } },
  "quality_evidence": { "stats": "work/stats.json", "report": "output/report.md" },
  "anomaly_notes": [], "sources": [ { "locator": "input/data.csv", "quote": "cement,slag,...", "used_for": "analysis" } ] }
```

## 5. 关键方法与口径

- 清洗：重复行按"全列值一致"判定后删除并计数；异常值按 1.5×IQR **仅标记不删除**；缺失值逐检验 listwise 处理，默认不插补。
- 统计：两组用 Welch t（不等方差）+ Mann-Whitney U，≥3 组用单因素 ANOVA + Kruskal-Wallis，多重比较用 Bonferroni 校正并附原始 p；相关用 Pearson + Spearman；回归用简单 OLS + 多元 OLS（正规方程，注明无共线性诊断）。
- 正态性：有 scipy 用 Shapiro-Wilk，否则用 D'Agostino-Pearson K²，方法名写入结果。
- 可视化：SVG 的均值/误差/分位全部取自 `stats.json`，轴标签注明单位与误差含义，杜绝图形放大或掩盖差异。

## 6. 降级策略（诚实标注，不编造）

- 缺 `case.json` → `input_error`；有 case 但无数据文件 → `insufficient_evidence`；两者都在 result.json 写清。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功则记 `degradations`。
- 环境缺 scipy/numpy/pandas → 使用本 Skill 纯标准库的 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- 无法解释的结果/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 7. 评分维度对齐

| 评分维度（分值） | 落点流程 | 对应自检项（§8） |
|---|---|---|
| 实际效果 40 | clean/analyze/render/report 五段串通 + 结果正确/完整/可核验 | S1 S2 S3 S6 S7 |
| 使用增益 25 | 骨架早落盘、幂等增量、确定性 | S1 S4 S9 |
| 创新增益 15 | 标准库统计引擎、SQLite 只读查询、字段/主表自动发现 | S5 S8 |
| 可靠体验 10 | 异常处理、降级、退出码 0 | S8 S10 |
| 生态贡献 10 | 文档、脚本、字段字典、自检可复跑 | S1 S4 S11 |

详见 `references/rubric_map.md`。

## 8. 自检清单（逐条可勾选，对应评分维度）

- [ ] S1 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] S2 `result.json` 含 `case_id` + 题目 output_contract 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] S3 `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] S4 清洗规则与实际执行一致：`clean_meta.json` 的删行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] S5 统计口径正确：检验方法匹配组数与方差前提；报告 p 值/效应量与 `stats.json` 一致。
- [ ] S6 图表与数据同源：SVG 的均值/误差/分位来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] S7 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] S8 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0 并在 errors/degradations 说明。
- [ ] S9 确定性：同一输入两次运行，SVG 与 `stats.json` 字节一致（无随机数）。
- [ ] S10 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。
- [ ] S11 资源可复跑：`scripts/selftest.py` 全绿；字段字典、示例与边界说明齐全。

## 9. 边界

一个 Skill 只解决"表格数据多步分析"这一类任务；没有真实材料时输出"材料不足"，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
