---
name: stat-analysis-02-evidence
description: 统计分析与结果解读（analysis-02，证据溯源强化版）：对带缺失/异常/重复的科研数据完成确定性清洗、描述统计、假设检验（先正态性/方差齐性）、效应量与置信区间，逐条结论给出 locator + 逐字 quote 的 evidence_map，交付可复核的数字、图表、代码与证据。仅在需要可复算、可追溯的统计结论时使用；不负责实验设计、样本量估算与论文写作。
version: 1.0.0
---

# 统计分析与结果解读（stat-analysis-02-evidence）

输入是科研数据 + 数据字典 + 任务说明。产出：清洗表、描述统计、假设检验、效应量、
置信区间与结果解释，全部带证据定位与运行日志。核心纪律：**给出结果，也保留计算依据**；
检验方法与数据分布/设计匹配；**每条结论都可指回材料的逐字片段（locator + quote）**；
不编造缺失软件状态或科学数据。

## 0. 安装位置与命令约定

本 Skill 由评测器安装在 `/skills/weekly-skill`；**所有命令先 `cd /skills/weekly-skill` 再执行**。
不要依赖当前工作目录。若路径不确定，先定位：

```bash
ls -d /skills/* 2>/dev/null; find / -maxdepth 4 -name SKILL.md 2>/dev/null | head
```

确认后，本文所有命令统一用相对路径 `scripts/core_impl.py`，如
`cd /skills/weekly-skill && python scripts/main.py run`。默认 `--input /input`、
`--output /app/output`、`--work /app/work`。

## 1. 输入契约

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）；这是**权威契约**，字段名以它为准。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 材料：csv/tsv/json/sqlite(.db)/xlsx 等；`environment/resources.yaml`
  声明可用工具（scientific-python、SQLite）。
- 编码按 `utf-8-sig → utf-8 → gb18030 → latin-1` 容错；单文件读取上限 4MB。
- 预算：64 次模型请求 / 900 秒 / 16384 tokens 每次回复；脚本只依赖标准库，
  scipy/numpy 可选（存在则用于交叉核对，不存在则用内置实现）。

## 2. 输出契约（必读）

必须产出 `/app/output/result.json`，顶层 = `case_id` + `output_contract` **全部字段**
（逐键回填，缺值 `null`/`[]`，不改名、不删未知键）+ 信封：

```text
case_id           # 从 case.json 原样复制，缺失写 "unknown-case"
status            # ok | partial | insufficient_evidence | input_error
sources[]         # {source_id, path, kind, rel_path, excerpt}
uncertainty[]     # 不确定项与缺口
change_log[]      # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
evidence_map[]    # 每条结论：claim + locator + 逐字 quote（本版重点）
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑，环境可渲染时补 PNG/PDF）、`quality_checks.json`、`analysis_code.py`。

## 3. 强制流程（先骨架，后增量；时间预算）

**前 3 分钟内必须先落盘一版 result.json 骨架（`init`），之后只做增量改进**，宁交
不完整但合法的结果，不零产物。

```bash
cd /skills/weekly-skill && python scripts/main.py init       # ① 3 分钟内必做：索引 + partial 骨架
cd /skills/weekly-skill && python scripts/main.py survey     # ② 材料/工具清单
cd /skills/weekly-skill && python scripts/main.py inspect    # ③ 数据字典 + 每表 schema/列类型
# ④ 读 task.md/case.json 确定分析问题与设计（两组?三组?配对?相关?单位?）
cd /skills/weekly-skill && python scripts/main.py analyze    # ⑤ 确定性清洗+描述+检验+效应量+CI
# ⑥ 依据设计复核/补写解释（可用 scipy 交叉核对 p 值），写入 result.json
cd /skills/weekly-skill && python scripts/main.py build      # ⑦ 汇总 result.json + 主产物 + evidence_map
cd /skills/weekly-skill && python scripts/main.py selfcheck  # ⑧ 复检
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | /app/output/result.json 骨架 + /app/work/index.json | ≤3 分钟 |
| ②③ survey/inspect | /input | 材料清单 + schema.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | 分析计划（设计/单位/假设） | 若干次请求 |
| ⑤ analyze | /input + ④ | /app/work/analysis.json + cleaned CSV + figures | 确定性，秒级 |
| ⑥ 解释补写 | ⑤ 结果 | result.json 的 interpretation/uncertainty | 剩余请求 |
| ⑦⑧ build/selfcheck | /app/work | /app/output 全部产物 + 校验 | 分钟级 |

## 4. 分析规范（硬要求，对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失（计数+比例，不当 0）；异常值（IQR 1.5 标记，不静默删除）；重复
   （去重前后 n + 保留规则）。动作全进 change_log 与 quality_checks.json。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis；写口径与单位。
4. 假设检验前先做正态性（有 scipy 用 shapiro，无则 Shapiro-Francia 近似）与
   方差齐性（Brown-Forsythe）。选检验：正态+齐→Student t/ANOVA；正态+不齐→Welch；
   非正态/小样本→Mann-Whitney/Kruskal-Wallis；**≥3 组禁止两两 t**；多重比较用
   Bonferroni 或 BH 校正并留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA eta²/omega²、
   分类 Cramér's V；写口径与置信区间。
6. 置信区间：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z）。
   CI 是参数覆盖区间，不误读为个体取值范围。
7. 结果解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写"证明有效"；
   标注适用条件、剩余不确定性与需人工确认项。

## 5. 证据溯源规范（本版设计取舍）

每条结论/关键数值/图元必须写入 `evidence_map`，每条含 `claim`、`locator`、`quote`、
`quote_kind`、`method`、`value`、`unit`、`status`。要点：

- 数据来源结论的 `quote` 引用原始文件**逐字片段**（表头 + 前若干单元格 / 原文句子）；
- 派生数值结论（mean/p/效应量/CI）`locator` 指向 `cleaned.csv#col[...]#rows[...]`，
  `quote` 引用其数据来源，`method` 写计算式；`quote_kind` 标 `verbatim`/`computed`/`note`；
- 冲突不隐藏：同一指标来源不一致时都记录并标 `source_conflict`；
- 缺失/无法判断写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。

## 6. 自检清单（逐条对应评分维度，交付前勾选）

- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] status 诚实：材料不足=insufficient_evidence，输入坏=input_error
- [ ] 缺失/异常/重复七类情形都有明确计数与处理规则（quality_checks.json）
- [ ] 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] 每个关键数值有 locator+quote 证据或计算依据（evidence_map 全部覆盖）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行结果一致（无随机数，确定性）
- [ ] 图表为可编辑 SVG（环境可渲染时补 PNG/PDF）；代码可在 /app/output 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录

## 7. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON/坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| scipy 不可用 | 用脚本内置近似（Shapiro-Francia、正态近似 p），标口径 |
| 脚本不可运行 | 手写合法 result.json（见下），退出码恒 0 |

## 8. 脚本不可用时的保底交付（必读）

若 `cd /skills/weekly-skill` 失败、`python` 不可用或脚本报错，**不要交零产物**：
`cat /input/case.json` 核对契约键与 case_id，手写一份合法但不完整的 result.json：

```json
{"case_id": "<从 case.json 原样复制>", "status": "partial",
 "result": {}, "quality": [], "descriptive": [], "inferential": [],
 "effects": [], "interpretation": {"note": "脚本未运行，仅手工骨架"},
 "sources": [{"source_id": "S1", "path": "/input/case.json", "kind": "text"}],
 "uncertainty": ["脚本未运行，仅手工兜底"], "change_log": [],
 "errors": ["script_unavailable: <实际错误>"], "degradations": ["hand-written fallback"],
 "pending_human": []}
```

写完自检：`python -c "import json;json.load(open('/app/output/result.json'))"`。
**宁可 partial，不要零产物。**

## 9. 参考文件

- `references/stats_checklist.md`：统计口径、检验选择表、效应量/CI 解释、七类情形、常见错误。
- `references/contract_notes.md`：字段级契约、证据溯源结构、复现要求、降级规则、评分维度映射。
- `scripts/selftest.py`：标准库自检（统计原语 + 端到端 + 空输入/坏 JSON 降级），退出码 0 为通过。

## 10. 版本与维护

- 1.0.0 首版：确定性标准库统计库 + 清洗/描述/推断/效应量/CI 流水线 + 契约回填 +
  逐字 evidence_map 溯源 + 自检。新增能力须对应评分维度；交付前跑
  `python scripts/selftest.py` 确认退出码 0。
