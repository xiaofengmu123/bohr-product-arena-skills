---
name: stat-analysis-02
description: 统计分析与结果解读（analysis-02）。对带缺失/异常/重复的科研数据（CSV/TSV/JSON/SQLite）完成确定性清洗、描述统计、假设检验（先正态性/方差齐性再选法）、效应量与置信区间，并交付可复核的 result.json、SVG 图、报告、代码与结构化 limitations/uncertainty。仅在需要可复算、可追溯的统计结论时使用；不负责实验设计、样本量估算与论文写作。
version: 2.0.0
---

# 统计分析与结果解读（stat-analysis-02）

把「一份脏数据」变成「能进报告、能被复核」的统计结论。核心纪律：**给出结果，也保留计算依据**；
检验方法与分布/设计匹配；缺数据、单位不明、软件缺失时如实标注 limitations，不编造。

## 1. 角色与何时使用

- 用：两组/多组差异、配对比较、相关分析、描述统计；交付清洗表 + 描述 + 推断 + 效应量 + CI + 解释。
- 不用：实验设计、样本量估算、论文写作；一次性聊天问答不需要本 Skill。
- 三个固定案例为 UCI 风格数据（Concrete/Iris/Wine 快照，可能为 CSV 或 SQLite），本 Skill 通用处理。

## 2. 输入契约

- `/input/case.json`：`case_id`、`task_instruction`、`output_contract`（可能含 data_dictionary）。这是**权威契约**。
- `/input/task.md`：题目与评分要求；`/input/` 下可能有 csv/tsv/json/sqlite/xlsx/docx/pdf。
- `environment/resources.yaml` 声明工具（scientific-python、SQLite 等）。
- 工作目录 `/app/work`；**最终产物写 `/app/output`**。
- 预算：64 次模型请求 / 900 秒 / 16384 tokens 每次回复；不联网；脚本只用标准库。
- 安装位置：评测器把 Skill 装在 `/skills/weekly-skill`。**所有命令先 `cd /skills/weekly-skill` 再执行**；
  若目录不同，用 `ls -d /skills/*` 定位后替换。脚本一律用相对路径 `scripts/xxx.py`。

## 3. 输出契约（必读）

必须产出 `/app/output/result.json`，顶层 = `case_id` + `output_contract` **全部字段**（逐键回填，缺值 `null`/`[]`，不改名、不删未知键）+ 信封：

```text
case_id        # 从 case.json 原样复制；缺失写 "unknown-case"
status         # ok | partial | insufficient_evidence | input_error
result         # {design, summary}
quality[]      # 七类情形 + 假设检查
descriptive[]  # 每列 n/mean/median/sd/min/max/Q1/Q3/IQR/skew/kurt + 组内分层
inferential[]  # 正态性/齐性/主检验/两两比较（带证据与方法口径）
effects[]      # 效应量 + CI（带口径与证据）
interpretation # summary / statistical_vs_practical / unit / notes
limitations[]  # 结构化 {id,severity,affects,message}（本版强化）
sources[]      # {source_id, path, kind, rel_path}
uncertainty[]  # 结构化 {id,kind,severity,affects,message}（本版强化）
change_log[]   # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑；环境可渲染时补 PNG/PDF）、`quality_checks.json`、`analysis_code.py`（+ 复制的 `statlib.py`）。

## 4. 执行流程（先骨架后增量；总 900 秒 / 64 请求）

**前 3 分钟内必须先落盘一版 result.json 骨架（`init`），之后只做增量改进**，宁交不完整但合法，不零产物。

```bash
cd /skills/weekly-skill
python scripts/main.py init       # ① ≤3 分钟必做：索引 + partial 骨架 → /app/output/result.json + /app/work/index.json
python scripts/main.py survey     # ② 材料/工具清单 → /app/work/survey.json
python scripts/main.py inspect    # ③ 数据字典 + 每表 schema/列类型 → /app/work/schema.json
# ④ 读 task.md/case.json 定设计：两组?三组?配对?相关?单位? 把决策写 /app/work/plan.json
python scripts/main.py analyze    # ⑤ 确定性清洗+描述+检验+效应量+CI → /app/work/analysis.json + cleaned/ + figures/
# ⑥ 依据设计复核/补写解释（可选 scipy crosscheck），把人工结论写 /app/work/interpretation.json
python scripts/main.py build      # ⑦ 汇总 result.json + 报告 + quality_checks + analysis_code
python scripts/main.py selfcheck  # ⑧ 复检
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | result.json 骨架 + index.json | ≤3 分钟 |
| ②③ survey/inspect | /input | survey.json + schema.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | /app/work/plan.json | 若干次请求 |
| ⑤ analyze | /input + ④ | analysis.json + cleaned + figures | 秒级 |
| ⑥ 解释补写 | ⑤ | interpretation.json | 剩余请求 |
| ⑦⑧ build/selfcheck | /app/work | /app/output 全部产物 | 分钟级 |

`plan.json`（Agent 在 ④ 写，可缺省自动推断）：`task_type`(group_comparison|correlation|paired_comparison|descriptive_only)、`data_file`、`group_column`、`outcome_columns`、`id_column`、`predictor_columns`、`alpha`、`mc_method`(bonferroni|bh)。
`interpretation.json`（Agent 在 ⑥ 写，可缺省）：`interpretation`(dict 合并)、`uncertainty`(list 追加)、`pending_human`(list 追加)、`status`(覆盖)。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] `result.json` 可解析，含 `case_id` + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] `status` 诚实：材料不足=insufficient_evidence，输入坏=input_error
- [ ] 缺失/异常/重复七类情形都有计数与处理规则（quality_checks.json）
- [ ] 检验方法与分布/设计匹配；先正态性/方差齐性；≥3 组未用两两 t；多重比较已校正（保留 p_raw/p_adj）
- [ ] 每个关键数值有 locator + quote 证据或计算依据（cleaned.csv + 代码 + method_note）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致；CI 未误读为个体范围
- [ ] 同输入两次运行结果一致（无随机数，确定性）；`python scripts/selftest.py` 退出码 0
- [ ] 图表为可编辑 SVG；代码可在 /app/output 复跑（analysis_code.py + statlib.py）
- [ ] limitations/uncertainty 结构化且显式（缺数据不编造；单位不明写 unit_unknown）
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录

## 6. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 / 无表可载 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON / 坏编码 | 记 errors/degradations，继续处理其余材料 |
| xlsx/docx/pdf 等标准库不可读 | survey/inspect 标 `unsupported`，用运行时 pandas 转 CSV 后重跑 inspect/analyze |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标 `not_applicable`，不给 p，说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜 μg/mg、cm/mm |
| scipy 不可用 | 用内置近似（AS R94 Shapiro-Wilk 等），`method_note` 标近似；可 crosscheck 时复核并记差异 |
| 脚本不可运行 | `cat /input/case.json` 核对契约键，手写合法 partial result.json（含 case_id+status+信封），退出码恒 0 |

兜底最小结果（脚本失败时手写，宁可 partial 不零产物）：

```json
{"case_id": "<原样>", "status": "partial",
 "result": {}, "quality": [], "descriptive": [], "inferential": [],
 "effects": [], "interpretation": {"note": "脚本未运行，仅手工骨架"},
 "sources": [{"source_id": "S1", "path": "/input/case.json", "kind": "text"}],
 "uncertainty": ["脚本未运行，仅手工兜底"], "change_log": [],
 "errors": ["script_unavailable"], "degradations": ["hand-written fallback"],
 "pending_human": []}
```

写完自检：`python -c "import json;json.load(open('/app/output/result.json'))"`。

## 7. 参考文件与维护

- `references/stats_checklist.md`：统计口径、检验选择表、效应量/CI 解释、七类情形、常见错误。
- `references/contract_notes.md`：字段级契约、证据溯源、降级规则、评分维度映射、本版侧重点。
- `python scripts/statlib.py` 运行数学库自检（退出码 0）；`python scripts/selftest.py` 运行端到端自检（空输入/坏输入/确定性）。
- 版本 2.0.0：在 1.x 完整流水线基础上，把 limitations/uncertainty 结构化显式化，强化缺数据不编造与单位不明不猜值。
