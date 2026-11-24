---
name: research-data-pipeline
description: 科研表格数据多步分析流水线（发现→校验→画像→清洗→统计→可视化→报告）。以 manifest 为唯一事实源，类型化中间表示贯穿各阶段，显式契约对账，输出可复核的 result.json、report.md 与可编辑 SVG。当任务要求对 CSV/TSV/JSON/SQLite 科研数据完成缺失/异常/重复处理、组间比较、相关分析、作图并生成可回算报告时使用。
version: 2.0.0
---

# 科研数据多步分析流水线（research-data-pipeline）

## 1. 角色与何时使用

把"原始表 + 一句需求"的科研数据分析编排成可复核的六段流水线：**发现与校验 → 画像 → 清洗 → 统计 → 可视化 → 契约对账**。它让 Agent 稳定产出有证据链、可重跑、失败可解释的结果，而不是只剩结论和截图。

适用：CSV/TSV/JSON/SQLite 表格数据；缺失值、异常值、重复记录处理；分组比较与假设校验；相关分析；图表选型；报告数字回填。不适用：纯聊天问答、无数据的任务、需外部数据库或网络的实时查询。

**架构（与单一大脚本实现明显不同）**：本包采用"阶段注册表 + manifest 唯一事实源 + 类型化中间表示(IR) + 显式契约对账"。每个阶段写盘一个 IR 工件并把路径与 SHA-256 写入 `work/manifest.json`；下游阶段从 manifest 解析路径并校验哈希后再消费，绝不依赖上游硬编码的路径。`output_contract` 的回填集中在独立的 `reconcile` 阶段完成，而不是散落在各阶段里。

## 2. 目录结构

```text
research-data-pipeline/
├── SKILL.md                 # 本文件
├── scripts/
│   ├── main.py              # 薄 CLI 入口（<1KB），只解析参数并调度 core.main()
│   ├── core.py              # 引擎：阶段注册表、IR、统计、SVG、契约对账
│   └── selftest.py          # 确定性自检（合成数据，非正式案例）
└── references/
    └── rules.md             # 统计口径决策表、降级矩阵、交付检查清单
```

全部 Python 只用标准库；无需 pip 安装。

## 3. 输入契约（先发现，再假设）

- 运行时原始输入在 `/input/`，至少含 `case.json`（任务元信息、`case_id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料按约定存放于 `/input` 下（CSV/TSV/JSON/SQLite，扩展名自动识别）。
- 工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。
- 扫描输入目录，不假设固定文件名/键名；`case.json` 缺 `output_contract` 时用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录。
- 主表/分组列/目标列自动发现，可用 `--group`、`--targets` 覆盖（以 `task.md` 为最高优先）。

## 4. 输出契约

必须产出 `/app/output/result.json`，包含：

- `case_id`：取自 case.json 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注；
- **题目 `output_contract` 的全部字段**：原样回填、不改名、不删未知键；缺值给 `null`/`[]`；
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值；
- `main_outputs`、`sources`（每项含 locator + 逐字 quote）、`uncertainty`、`pending_human`、`errors`、`degradations`、`change_log`（逐阶段变更）、`runtime`、`reproducibility`（manifest 路径与工件哈希）。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`，可回算）+ 可编辑 `figure_group_box.svg`、`figure_corr_scatter.svg`。中间 IR：`work/case.json`、`work/profile.json`、`work/cleaned.csv`、`work/clean_meta.json`、`work/stats.json`、`work/manifest.json` 一并保留。

## 5. 执行流程与预算（总 900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟必须落盘一版 `partial` 结果骨架（scaffold 阶段即写 result.json），之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。全部脚本幂等、确定性，可安全重跑。

| 阶段 | 命令（相对路径） | 预算 | 输入 → 输出 |
|---|---|---|---|
| 0 scaffold | `python3 -B scripts/main.py --stage scaffold` | 0–3 分钟，≤4 请求 | 发现 `/input`、解析 case.json+task.md、写 `work/case.json`；**立即写 result.json 骨架**（status=partial，全部契约键为 null） |
| 1 profile | `--stage profile` | 3–6 分钟，≤8 请求 | 读入全部表 → 选主表、字段字典、类型/行数/缺失/异常初检 → `work/profile.json` |
| 2 clean | `--stage clean` | 6–9 分钟，≤12 请求 | 去重、缺失标记、IQR 异常标记（只记录规则与行数影响，不静默丢数据）→ `work/cleaned.csv`+`work/clean_meta.json` |
| 3 analyze | `--stage analyze` | 9–12 分钟，≤25 请求 | 描述统计 + Welch t / Mann-Whitney / ANOVA F / Kruskal-Wallis / Pearson / Spearman，附前提与近似说明 → `work/stats.json` |
| 4 render | `--stage render` | 12–13 分钟，≤8 请求 | SVG + `report.md`（数字全部从 `stats.json` 回填） |
| 5 reconcile | `--stage reconcile`（默认） | 13–15 分钟，≤7 请求 | 核对报告数字与 `stats.json` 同源；回填全部契约字段；定 status；自校验 JSON/SVG 后最终落盘 |

一次完整运行：`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`（等价于 `--stage reconcile`）。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + 题目 `output_contract` 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] `status` 诚实：无数据=`insufficient_evidence`、输入坏=`input_error`、部分失败=`partial`。
- [ ] 清洗规则与实际执行一致：`n_raw − n_clean == duplicates.rows_removed`；异常只标记不删。
- [ ] 统计口径正确：检验方法匹配组数与方差前提；报告 p 值/效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的分位/散点/回归线来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0，并在 `errors/degradations` 说明。
- [ ] 确定性：同一输入两次运行，`stats.json` 与 SVG 字节一致（无随机数）。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。
- [ ] 运行 `python3 -B scripts/selftest.py` 全部 PASS。

## 7. 降级策略（如实标注 limitations，不编造）

- 缺 `case.json` → `status=input_error`；有 case 但无数据文件 → `insufficient_evidence`；两者都写清 `errors`。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gb18030，降级成功则记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 不受影响：手写统计（t/F/χ² 经不完全 beta/gamma 实现），图用纯标准库 SVG。
- 无真实材料/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 8. 证据溯源

每个关键结论、数值、图元都指回材料或计算工件的**逐字片段**：`sources[]` 中每项含 `locator`（如 `/input/data.csv`、`work/stats.json`）+ `quote`（原文字符串，≤320 字符）。冲突来源不隐藏，同一指标多个来源都记录。计算结论的 quote 为 `stats.json` 中对应 JSON 片段。不虚构引用、DOI、数值。

## 9. 边界

一个 Skill 只解决"表格数据多步分析"这一类任务；没有真实材料时输出"材料不足"，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
