---
name: stat-analysis-02
description: 统计分析与结果解读（analysis-02）：对带缺失/异常/重复的科研数据完成清洗、描述统计、假设检验（先正态性/方差齐性，再按分布与设计选检验）、效应量与置信区间，并交付可复核的数字、图表、代码与证据定位。仅用于需要可复算、可追溯统计结论的任务；不负责实验设计、样本量估算与论文写作。
version: 2.0.0
---

# 统计分析与结果解读（stat-analysis-02）

## 1. 角色与何时使用

本 Skill 把“科研数据 → 可复核统计结论”的流程沉淀为可重复调用的确定性流水线。
适用：给定数据文件 + 数据字典 + 任务说明，产出清洗、描述统计、推断统计、效应量、
置信区间与结果解释，且每个关键数值都能回溯到数据中的**逐字片段（locator + quote）**。
不适用：实验方案设计、样本量估算、论文成稿；也不假装覆盖全部统计领域。

核心纪律：**先落盘合法结果，再增量改进**；检验方法与数据分布/设计匹配；缺失不当 0、
异常不静默删除、≥3 组不裸做两两 t；不给伪造的数字、引用或运行记录。

## 2. 输入契约（运行时）

- `/input/case.json`：权威契约，含 `case_id`、`task_instruction`、`output_contract`
  （可能含 `data_dictionary`）。字段名以它为准。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 材料：csv/tsv/json/sqlite(.db)/xlsx/xls/docx/pdf；工具清单在
  `environment/resources.yaml`（scientific-python、SQLite）。
- 工作目录 `/app/work`（中间产物）；**最终产物写 `/app/output`**。
- 预算：64 次模型请求 / 900 秒 / 16384 tokens 每次回复；不联网。
- 脚本只依赖标准库，先 `cd /skills/weekly-skill` 再执行；调用路径一律用相对路径
  `scripts/xxx.py`，不依赖当前工作目录。

## 3. 输出契约（必读）

必须产出 `/app/output/result.json`。顶层 = `case_id`（从 case.json 原样复制，缺失写
`"unknown-case"`）+ `output_contract` **全部字段**（逐键回填，缺值 `null`/`[]`，不改名、
不删未知键）+ 信封：

```text
case_id            # 原样复制
status             # ok | partial | insufficient_evidence | input_error
sources[]          # {source_id, path, kind, rel_path}
uncertainty[]      # 不确定项与缺口
change_log[]       # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
```

主产物（可编辑、可复现，全部由 `compute`/`package` 生成）：
`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`（可编辑；环境可渲染时再补
PNG/PDF）、`quality_checks.json`、`analysis_code.py`。

## 4. 执行流程（分步、每步输入输出、时间预算）

总预算 900 秒 / 64 次请求。**前 3 分钟内必须完成 ① ② 落盘一版 result.json 骨架，
之后只做增量改进**；宁可交合法但不完整的 partial，不零产物。

```bash
cd /skills/weekly-skill
python scripts/main.py probe      # ① 读 case.json + 材料清单 → work/index.json（秒级）
python scripts/main.py skeleton   # ② 写 result.json 骨架（契约回填 null/[]）→ ≤3 分钟必做
python scripts/main.py infer      # ③ 每表 schema/dtype/单位 → work/schema.json（秒级）
# ④ Agent 读 task.md/case.json，确定设计（两组?三组?配对?相关?回归?单位?），
#    编辑 work/plan.json（design/group_col/outcome_cols/target_col/alpha/correction），
#    或接受 python scripts/main.py plan 自动提议的 plan.json
python scripts/main.py plan       # ⑤ 生成/校验声明式分析计划 → work/plan.json
python scripts/main.py compute    # ⑥ 确定性清洗+描述+检验+效应量+CI → work/analysis.json + cleaned/*.csv + figures/*.svg
python scripts/main.py package    # ⑦ 汇总 result.json + report + quality_checks + code
python scripts/main.py verify     # ⑧ 复检（契约键、SVG、状态）
python scripts/main.py all        # 等价于按序执行 probe→skeleton→infer→plan→compute→package→verify
python scripts/selftest.py        # 交付前自检（退出码 0 表示通过）
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ①② probe/skeleton | /input | index.json + result.json 骨架 | 3 分钟内必做 |
| ③ infer | /input + index.json | schema.json | 秒级 |
| ④ 任务解读 | task.md/case.json/schema.json | 计划决策（写进 plan.json） | 若干次请求 |
| ⑤⑥ plan/compute | plan.json + 数据 | analysis.json + cleaned CSV + SVG | 确定性，秒级 |
| ⑦⑧ package/verify | analysis.json | /app/output 全部产物 + 校验 | 分钟级 |

声明式计划（plan.json）是本 Skill 与“整块 analyze”脚本的架构差异：LLM 只做任务解读
并写一个**计划文件**，计算由确定性代码按计划执行；换一份新数据只需改计划，不重写代码。
`compute` 可重复运行，同输入同输出（无随机数）。

## 5. 分析规范（对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失（计数+比例，不当 0）；异常（Tukey IQR 1.5 标记，不静默删除）；重复
   （去重前后 n + 保留规则）。动作全进 change_log 与 quality_checks.json。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis/均值 95% CI；写口径与单位。
4. 先做正态性（D'Agostino-Pearson K²，n≥20；小样本标注不适用并建议 scipy Shapiro-Wilk 交叉核对）
   与方差齐性（Brown-Forsythe）。选检验：两组正态+齐→Student t；正态+不齐→Welch；
   非正态/小样本→Mann-Whitney；≥3 组正态+齐→ANOVA；正态+不齐→Welch ANOVA；否则
   Kruskal-Wallis。**≥3 组禁止裸两两 t**；事后比较用 Bonferroni 或 BH 并保留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA η²/ω²、
   分类 Cramér's V；写口径与区间。
6. 置信区间：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z、
   回归斜率 t CI）。CI 是参数覆盖区间，不误读为个体取值范围。
7. 解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写“证明有效”；标注适用条件、
   剩余不确定性与需人工确认项。

## 6. 自检清单（逐条可勾选）

- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] status 诚实：材料不足=insufficient_evidence，输入坏=input_error，脚本失败=partial 且写明
- [ ] 缺失/异常/重复/统计假设/图表选择/代码运行/数据库字段七类情形都有计数与处理规则（quality_checks.json）
- [ ] 先做正态性/方差齐性；检验与分布/设计匹配；≥3 组未裸用两两 t；多重比较已校正并留校正前后 p
- [ ] 每个关键数值有 locator+quote 证据或计算依据（cleaned.csv + analysis_code.py + evidence_map）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行结果一致（无随机数；`selftest.py` 含确定性断言）
- [ ] 图表为可编辑 SVG（环境可渲染时补 PNG/PDF）；代码可在 /app/output 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录

## 7. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON/坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标“不适用”，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| 二值/小样本正态性 | K² 需 n≥20；小样本标不适用，建议 scipy Shapiro-Wilk 交叉核对 |
| scipy 可用 | 可另行交叉核对 p 值，但交付数字以 stdlib 确定性结果为准并写 method |
| 脚本不可运行 | 手写合法 result.json 骨架（含 case_id/status/契约键/信封），退出码恒 0 |

脚本兜底（若 `/skills/weekly-skill` 或 `python` 异常）：`cat /input/case.json` 核对契约，
手写一份合法但不完整的 result.json，再
`python -c "import json;json.load(open('/app/output/result.json'))"` 校验。**宁可 partial，不零产物。**

## 8. 参考文件与维护

- `references/stats_checklist.md`：统计口径、检验选择表、效应量/CI 解释、七类情形、常见错误。
- `references/contract_notes.md`：字段级契约、复现要求、降级规则、评分维度映射。

- 2.0.0 首版：标准库特殊函数（不完全 beta/gamma，t/F/χ² 精确 p）+ D'Agostino K² 正态性 +
  声明式计划 IR + 证据模型（locator+quote）+ 清洗/描述/推断/效应量/CI 流水线 + 契约回填与自检。
- 新增能力须对应评分维度；改动后先跑 `python scripts/selftest.py`，退出码须为 0。
