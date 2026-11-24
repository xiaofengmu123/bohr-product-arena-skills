---
name: sci-data-analysis-evidence
description: 科研数据多步骤分析 Skill（证据溯源强化版）。把“原始表 + 一句需求”编排为 导入→字段核对→清洗→统计检验→可视化→报告 六段流水线；每条关键结论与数值都带 locator+逐字 quote，产出 evidence_map.json 与可复核的 result.json。适用于 CSV/TSV/JSON/SQLite 科研数据的缺失/异常/重复处理、组间比较、相关分析与图表报告。
version: 1.1.0
---

# 科研数据多步骤分析 Skill（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 将一条“原始表 + 一句需求”的分析任务编排为可复核的六段流水线：**导入 → 核对字段 → 清洗 → 统计 → 可视化 → 报告**。与一般“只给结论和截图”的做法不同，本版把**证据溯源**提升为一等公民：`result.json` 与 `report.md` 中的每个关键结论、数值、图元都必须能指回材料中的 `locator + 逐字 quote`，并汇总为 `evidence_map.json`；缺失、异常、无法解释处都显式登记，不静默丢数据、不编造。

适用：CSV/TSV/JSON/SQLite 表格数据；缺失/异常/重复处理；分组比较与假设校验；相关分析；图表选型；报告数字回填。不适用：纯聊天问答、无数据的任务、需外部网络/私有数据库的一键脚本。

## 2. 输入契约（先发现，再假设）

- 运行时原始输入位于 `/input/`，至少含 `case.json`（任务元信息、`case_id/challenge_id/armiId/id`、可能的 `output_contract`）与 `task.md`（任务要求）；数据材料按约定存放于 `/input` 下（CSV/TSV/JSON/SQLite 等，也可能在子目录）。
- 工作目录 `/app/work`（中间产物），最终产物写入 `/app/output/`。先扫描输入目录列出真实文件与编码，不假设固定文件名/键名。
- 脚本用相对路径调用：入口 `scripts/main.py`（仅标准库），同目录依赖 `scripts/core_impl.py`，自检为 `scripts/selftest.py`。
- `case.json` 缺 `output_contract` 时，用默认三键 `analysis_results / quality_evidence / anomaly_notes` 并在 `degradations` 记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段：

- `case_id`：取 `case.json` 的 `case_id/challenge_id/armiId/id`，缺失时用输入目录名并标注。
- 题目 `output_contract` 的全部字段：原样回填、不改名、不删未知键；缺值给 `null`/`[]`。
- `status`：`ok | partial | insufficient_evidence | input_error`，按真实完成情况赋值。
- `sources`（每项含 `locator + quote`）、`uncertainty`（口径/近似/未决项）、`change_log`（逐步变更，含阶段与时间）、`pending_human`（需人工确认项）、`errors`、`degradations`、`runtime`（秒）。
- `evidence`：引用 `evidence_map.json` 条目 id 列表；`evidence_map.json` 为同级独立产物，见 §6。

主产物：`/app/output/report.md`（正文数字全部来自 `work/stats.json`，无手写数字）+ 可编辑 SVG（`figure_*.svg`）。环境另有 matplotlib/pandas/scipy 时可补 PNG/PDF 预览，但不得丢失 SVG 与重算依据。中间产物保留：`work/cleaned.csv`、`work/clean_meta.json`、`work/stats.json`、`work/profile.json`、`work/sources.json`。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

核心纪律：**前 3 分钟内必须落盘一版 `result.json` 骨架（status=partial），之后只做增量改进**；任何一步失败都不允许留下空 `/app/output`。全部脚本幂等，可安全重跑。

| 阶段 | 预算 | 步骤（输入 → 输出） |
|---|---|---|
| 0 骨架 | 0–3 分钟，≤4 请求 | 读 `/input/case.json`+`task.md` → 提取 case_id、output_contract、任务要求；立即 `python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work`，先写 `result.json` 骨架（status=partial，全部契约键为 null/[]） |
| 1 探查 | 3–6 分钟，≤8 请求 | `profile`：发现数据源、字段字典、类型/行数、缺失/重复/异常初检 → `work/profile.json`、`work/sources.json`（含 SHA-256 与编码）；读 task.md 确定主表/分组列/目标列，必要时 `--primary/--group/--targets` 覆盖 |
| 2 分析 | 6–10 分钟，≤25 请求 | `clean`（去重、缺失、异常，**只记录规则与行数影响，不静默丢数据**）→ `analyze`（描述统计 + Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis / Pearson，附前提与近似说明，每个统计量写证据条目）→ `render`（SVG + report.md + evidence_map.json） |
| 3 核对 | 10–13 分钟，≤20 请求 | 核对报告数字与 `stats.json` 同源、每条结论有 locator+quote；补异常说明与 uncertainty；`finalize` 定 status 并回填全部契约字段 |
| 4 自检 | 13–15 分钟，≤7 请求 | `python3 -B scripts/selftest.py --input /input --output /app/output --work /app/work`：JSON 可解析、SVG 可解析、契约键齐全、evidence 条目均有 locator+quote；修复后最终落盘；预留约 5 次请求应对重试 |

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] 骨架在前 3 分钟内落盘；后续只增量更新，不重写已定结果。
- [ ] `result.json` 含 `case_id` + 题目 output_contract 全部字段 + `status/sources/uncertainty/change_log/pending_human`。
- [ ] `status` 诚实：无数据=insufficient_evidence、输入坏=input_error、部分失败=partial。
- [ ] 清洗规则与实际执行一致：`clean_meta.json` 的删行数 == 原始行数 − `cleaned.csv` 行数；异常只标记不删。
- [ ] 统计口径正确：检验方法匹配组数与方差前提；报告 p 值/效应量与 `stats.json` 一致。
- [ ] 图表与数据同源：SVG 的均值/误差/分位来自 `stats.json`，轴标签注明单位与误差含义。
- [ ] 报告数字可回算：正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] **证据溯源**：`evidence_map.json` 每个条目含非空 `locator` 与逐字 `quote`；`quote` 是材料原文子串（文本源）或原始数据单元格值（数据源），禁止改写。
- [ ] 空输入/坏 JSON/缺列/类型不符/除零/编码错误均退出码 0 并在 errors/degradations 说明。
- [ ] 确定性：同一输入两次运行，SVG 与 `stats.json` 字节一致（无随机数、固定排序）。
- [ ] 不编造：缺失软件/数据 → `pending_human` 或 `not_reported`，绝不虚构数值、DOI、结论。

## 6. 证据溯源（本版强化方向）

`evidence_map.json` 是独立、机器可读的一等产物，条目 schema：

```json
{
  "schema_version": "1.1",
  "entries": [
    {
      "id": "E-001",
      "conclusion": "一句可核查的结论/数值声明",
      "claim_type": "observation|inference|rule|artifact|conflict|gap",
      "locator": "来源定位，如 data:iris.csv#col=sepal_length#rows=1..50 或 task.md#L23",
      "quote": "材料中的逐字原文子串，或原始数据单元格的原文值",
      "quote_source": "该 quote 取自哪个文件",
      "method": "如何从 quote 推出 conclusion（公式/规则名）",
      "confidence": "high|medium|low",
      "value": null
    }
  ]
}
```

- `claim_type` 分类：`observation`（读到的原始值）、`inference`（由统计/规则计算）、`rule`（清洗/筛选规则及其来源依据）、`artifact`（图元/报告段落对应的底层数据）、`conflict`（两个来源不一致，全部保留并标 `source_conflict`）、`gap`（缺失/未报告，记 `not_reported`）。
- 每个关键结论、p 值、效应量、清洗删行数、图元数值都必须有对应条目；正文/图表只引用已在 evidence_map 登记的数字。
- `quote` 必须逐字来自材料，不得转述或改写；无法取得原文时写 `pending_human` 并把 quote 置空，不得编造。

## 7. 降级策略（诚实标注，不编造）

- 缺 `case.json` → status=input_error；有 case 但无数据文件 → insufficient_evidence；两者都在 result.json 写清并进入 `pending_human`。
- 某列类型不符/全空 → 记入 `degradations`，跳过该列，继续其余列。
- 除零/空组/样本不足 → 该检验返回 `skipped/error` 并写 `uncertainty`，不产出伪造 p 值。
- 编码异常 → 依次尝试 utf-8-sig/utf-8/latin-1/gbk，降级成功则记 `degradations`。
- 环境缺 matplotlib/pandas/scipy → 使用本 Skill 纯标准库的 SVG 与手写统计（t/F/χ² 经不完全 beta/gamma 实现），核心交付不受影响。
- 无法解释的结果/口径争议 → 写入 `uncertainty` 与 `pending_human`，请求人工确认，不擅自定论。

## 8. 边界

一个 Skill 只解决“表格数据多步分析”这一类任务；没有真实材料时输出“材料不足”，不用示例填充；正式测试案例、白板与分数由平台生成，本 Skill 不预置、不伪造。
