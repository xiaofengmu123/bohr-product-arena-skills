---
name: stat-analysis-02-ir
description: 统计分析与结果解读（analysis-02，中间表示可复核变体）：对带缺失/异常/重复的科研数据完成清洗、描述统计、假设检验（先正态性/方差齐性再选检验）、效应量与置信区间，并以结构化中间产物（plan.json/data_model.json/stat_model.json/figure_model.json/evidence.json）保留全部计算依据，交付可复核的数字、图表、代码与证据。仅在需要可复算、可追溯的统计结论时使用；不负责实验设计、样本量估算与论文写作。
version: 2.0.0
---

# 统计分析与结果解读（中间表示可复核）

输入是科研数据 + 数据字典 + 任务说明。产出：清洗表、描述统计、假设检验、效应量、
置信区间与结果解释，全部带证据定位与运行日志。核心纪律：**给出结果，也保留计算依据**；
**每个关键数字都能追溯到中间产物中的公式、输入与 locator**；检验方法与数据分布/设计
匹配；不编造缺失软件状态或科学数据。

## 0. 安装位置与命令约定（Harbor）

本 Skill 由评测器安装在 `/skills/weekly-skill`；**所有命令先 `cd /skills/weekly-skill` 再执行**。
不要依赖当前工作目录（评测 cwd 常为 `/app/work`）。若路径不确定，先定位：

```bash
ls -d /skills/* 2>/dev/null; find / -maxdepth 4 -name SKILL.md 2>/dev/null | head
```

确认后统一用 `cd /skills/weekly-skill && python scripts/main.py <cmd>`；安装目录不同则替换
`/skills/weekly-skill`。脚本调用路径均为相对路径 `scripts/xxx.py`。

## 1. 输入契约

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）；这是**权威契约**，字段名以它为准。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 材料：csv/tsv/json/sqlite(.db)/xlsx 等表格由脚本直接读取；docx/pdf 由 Agent
  先用工具转成文本/csv 再交脚本，或手写结果。`environment/resources.yaml` 声明可用工具
  （scientific-python、SQLite 等，运行时若可用则用 scipy 交叉核对 p 值）。
- 工作目录 `/app/work`，**最终产物写 `/app/output`**。
- 预算：**64 次模型请求 / 900 秒 / 16384 tokens 每次回复**；不联网；脚本只依赖标准库。

## 2. 输出契约（必读）

必须产出 `/app/output/result.json`，顶层 = `case_id` + `output_contract` **全部字段**
（逐键回填，缺值 `null`/`[]`，不改名、不删未知键）+ 信封：

```text
case_id           # 从 case.json 原样复制，缺失写 "unknown-case"
status            # ok | partial | insufficient_evidence | input_error
sources[]         # {source_id, path, kind, rel_path}
uncertainty[]     # 不确定项与缺口
change_log[]      # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}   # 诊断与运行信息
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑，环境可渲染时补 PNG/PDF）、`quality_checks.json`、`analysis_code.py`。

## 3. 中间表示（本版核心设计取舍）

脚本把每个环节落成**结构化中间产物**（写 `/app/output/intermediate/`），result.json 与
报告都从这些产物装配，而不是临时拼数字。复核者只需重跑 `analyze` 即可复现同一组数字。

| 产物 | 内容 | 作用 |
|---|---|---|
| `index.json` | 输入文件清单（大小/类型） | 材料溯源 |
| `survey.json` | 材料/工具清单 | 能力盘点 |
| `data_model.json` | 每表 schema、列类型、缺失计数 | 字段级契约 |
| `plan.json` | 设计/假设/选定检验 | 方法决策依据 |
| `cleaning_model.json` | 缺失/异常/重复每一条规则与计数 | 清洗可复核 |
| `stat_model.json` | 每个统计量的公式、输入、全精度数值 | 数字可复算 |
| `figure_model.json` | 图类型/序列/编码 | 图可再生成 |
| `evidence.json` | locator + 逐字 quote + 计算依据 | 证据溯源 |
| `run_log.json` | 版本、计时、确定性 hash | 复现与一致性 |

## 4. 强制流程（先骨架，后增量；时间预算）

**前 3 分钟内必须先落盘一版 result.json 骨架（`init`），之后只做增量改进**，宁交不完整
但合法的结果，不零产物。

```bash
cd /skills/weekly-skill && python scripts/main.py init       # ① ≤3 分钟：索引 + partial 骨架
cd /skills/weekly-skill && python scripts/main.py survey     # ② 材料/工具清单
cd /skills/weekly-skill && python scripts/main.py inspect    # ③ 数据字典 + 每表 schema/列类型
# ④ 读 task.md/case.json 确定分析设计（两组?三组?配对?相关?单位?），补写 plan.json
cd /skills/weekly-skill && python scripts/main.py analyze    # ⑤ 清洗+描述+检验+效应量+CI（确定性）
# ⑥ 依据设计复核/补写解释（可用 scipy 交叉核对 p 值），回填 result.json 的契约字段
cd /skills/weekly-skill && python scripts/main.py build      # ⑦ 从中间产物装配 result.json + 主产物
cd /skills/weekly-skill && python scripts/main.py selfcheck  # ⑧ 复检
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | result.json 骨架 + index.json + plan.json 骨架 | ≤3 分钟 |
| ②③ survey/inspect | /input | survey.json + data_model.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | plan.json（设计/单位/假设） | 若干请求 |
| ⑤ analyze | /input + ④ | stat_model.json 等 + cleaned CSV + figures | 确定性，秒级 |
| ⑥ 解释补写 | ⑤ 结果 | result.json 的 interpretation/uncertainty | 剩余请求 |
| ⑦⑧ build/selfcheck | 中间产物 | /app/output 全部产物 + 校验 | 分钟级 |

## 5. 分析规范（硬要求，对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失（计数+比例，不当 0）；异常值（IQR 1.5 标记，不静默删除）；重复
   （去重前后 n + 保留规则）。动作全进 change_log、cleaning_model.json 与 quality_checks.json。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis；写口径与单位。
4. 假设检验前先做正态性（Shapiro-Wilk，Royston 1995 AS R94）与方差齐性
   （Brown-Forsythe）。选检验：正态+齐→Student t/ANOVA；正态+不齐→Welch；
   非正态/小样本→Mann-Whitney/Kruskal-Wallis；**≥3 组禁止两两 t**；多重比较用
   Bonferroni 或 BH 校正并留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA eta²/omega²、
   分类 Cramér's V；写口径与置信区间。
6. 置信区间：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z）。
   CI 是参数覆盖区间，不误读为个体取值范围。
7. 结果解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写"证明有效"；
   标注适用条件、剩余不确定性与需人工确认项。每个关键结论给 `locator + 逐字 quote`。

## 6. 自检清单（逐条对应评分维度，用 selfcheck 落地）

- [ ] `python scripts/statlib.py` 单元自检退出码 0；`python scripts/main.py selfcheck` 退出码 0
- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] status 诚实：材料不足=insufficient_evidence，输入坏=input_error，不编造
- [ ] 缺失/异常/重复七类情形都有明确计数与处理规则（quality_checks.json / cleaning_model.json）
- [ ] 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] 每个关键数值有 locator+quote 证据或计算依据（stat_model.json + evidence.json + seed）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致；中间产物含全精度数值
- [ ] 同输入两次运行结果一致（无随机数，确定性；run_log.json 含 hash）
- [ ] 图表为可编辑 SVG（figure_model.json 可再生成）；代码可在 /app/output 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录、benchmark 分数

## 7. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON/坏编码/超 2MB | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| scipy 不可用 | 用脚本内置精确算法（Shapiro-Wilk/BF/t/F/χ²），标口径 |
| xlsx 读取失败 | 记 degradation，请 Agent 用 pandas 转 csv 后重跑 |
| 脚本不可运行 | 手写合法 result.json（见 §8），退出码恒 0 |

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
- `references/contract_notes.md`：字段级契约、中间表示规范、复现要求、降级规则、评分维度映射。

## 10. 版本与维护

- 2.0.0：在 1.x 确定性统计库基础上引入**中间表示可复核层**（§3 九个结构化产物），
  全精度数值落盘，result.json 由中间产物装配；statlib 内置精确 Shapiro-Wilk 等算法。
- 新增能力须对应评分维度；脚本内自检 `python scripts/statlib.py` 退出码 0。
