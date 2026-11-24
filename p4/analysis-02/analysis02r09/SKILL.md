---
name: stat-analysis-02
description: 统计分析与结果解读（analysis-02）：对科研数据完成确定性清洗、描述统计、假设检验（先正态性/方差齐性）、效应量与置信区间，交付可复核的 result.json、清洗表、图表、代码与证据。仅在需要可复算、可追溯的统计结论时使用；不负责实验设计、样本量估算与论文写作。
version: 1.1.0
---

# 统计分析与结果解读（stat-analysis-02）

核心纪律：**给出结果，也保留计算依据**；检验方法与数据分布/研究设计匹配；不编造缺失的软件状态或科学数据。

## 1. 角色与何时使用

- 用途：对带缺失/异常/重复的科研数据完成"清洗 → 描述统计 → 假设检验 → 效应量 → 置信区间 → 结果解释"，全程可复核。
- 触发时机：需要可复算、可追溯的统计结论（两组/多组比较、相关、配对、单样本）时使用。
- 不适用：实验方案设计、样本量估算、论文写作；也不做一次性脚本或纯问答。

## 2. 输入契约（运行时事实，不要假设）

- `/input/case.json`：权威契约，含 `case_id`、`task_instruction`、`output_contract`（可能含 `data_dictionary`）。字段名以它为准。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 其余材料：`csv/tsv/json/sqlite(.db)/xlsx/xls/docx/pdf`；`environment/resources.yaml` 声明可用工具（scientific-python、SQLite 等）。
- 工作目录 `/app/work`；**最终产物写 `/app/output`**。
- 预算：**64 次模型请求 / 900 秒 / 16384 tokens 每次回复**；脚本只依赖 Python 标准库；不联网。

## 3. 输出契约（必读）

必须产出 `/app/output/result.json`，顶层 = `case_id` + `output_contract` **全部字段**（逐键回填，缺值 `null`/`[]`，不改名、不删未知键）+ 信封：

```text
case_id        # 从 case.json 原样复制，缺失写 "unknown-case"
status         # ok | partial | insufficient_evidence | input_error
sources[]      # {source_id, path, kind, rel_path}
uncertainty[]  # 不确定项与缺口
change_log[]   # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`（可编辑）、`quality_checks.json`、`analysis_code.py`。

## 4. 执行流程（骨架优先，增量改进）

**前 3 分钟内必须 `init` 落盘一版 partial 结果骨架，之后只做增量改进；宁可交不完整但合法的结果，不零产物。**

```bash
cd /skills/weekly-skill
python scripts/analyze.py init       # ① ≤3min：索引 /input + 写 result.json 骨架
python scripts/analyze.py inspect    # ② 材料/工具清单 + 每表 schema/列类型
# ③ 读 task.md/case.json 确定分析问题与设计（两组?三组?配对?相关?单位?）
#    把结论写入 /app/work/plan.json：group_col/value_col/value_col2/design/value_unit
python scripts/analyze.py analyze    # ④ 确定性清洗+描述+检验+效应量+CI+图
python scripts/analyze.py build      # ⑤ 汇总 result.json + 报告 + 质量检查 + 代码
python scripts/analyze.py selfcheck  # ⑥ 复检契约与产物
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | result.json 骨架 + index.json | ≤3 分钟 |
| ② inspect | /input | schema.json（列类型/缺失/去重基数） | 约 1 分钟 |
| ③ 任务解读 | task.md/case.json | plan.json（设计/单位/假设） | 若干请求 |
| ④ analyze | /input + plan | cleaned CSV + analysis.json + SVG | 秒级 |
| ⑤ build | ④ | result.json + report + quality_checks | 分钟级 |
| ⑥ selfcheck | /app/output | 校验通过/问题清单 | 秒级 |

`plan.json` 是本 Skill 与 Agent 的分工点：脚本负责机械计算（确定性、可复算），Agent 负责语义判断（哪个是结果列、哪个是分组列、研究设计是什么、单位是什么）。不写 plan.json 时脚本按保守启发式自动推断，并在 `uncertainty` 中标注。

## 5. 分析规范（硬要求，对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失（计数+比例，不当 0）；异常值（IQR 1.5 标记，不静默删除）；重复（去重前后 n + 保留规则）。动作全进 `change_log` 与 `quality_checks.json`。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis；写口径与单位。
4. 检验前先正态性（Shapiro-Wilk，脚本为 Royston 近似）与方差齐性（Brown-Forsythe）。选检验：正态+齐→Student t/ANOVA；正态+不齐→Welch；非正态/小样本→Mann-Whitney/Kruskal-Wallis；**≥3 组禁止两两 t**；多重比较 Bonferroni 校正并留校正前后 p。
5. 效应量：Cohen's d/Hedges' g（方向+CI）、Glass Δ、ANOVA η²/ω²、分类 Cramér's V；写口径。
6. 置信区间：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z）。CI 是参数覆盖区间，不误读为个体取值范围。
7. 结果解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写"证明有效"；标注适用条件、剩余不确定性与需人工确认项。
8. 证据溯源：每个关键数值给 locator（如 `cleaned#species=setosa`）与逐字 quote 或计算依据；脚本自复制到 `analysis_code.py` 供复跑。

## 6. 自检清单（逐条可勾选）

- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] status 诚实：材料不足=insufficient_evidence，输入坏=input_error
- [ ] 七类情形（缺失/异常/重复/统计假设/图表选择/代码运行/数据库字段）都有计数与规则（quality_checks.json）
- [ ] 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] 每个关键数值有 locator+quote 或计算依据（cleaned.csv + 代码 + 无随机种子）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行数字一致（确定性，无随机数）
- [ ] 图表为可编辑 SVG；代码可在 /app/output 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录

## 7. 降级策略（诚实优先）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON/坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| xlsx/xls | 标准库无法读，degradations 注明需 pandas 转换后再跑 |
| 脚本不可运行 | 手写合法 result.json（见下），退出码恒 0 |

脚本不可用时的保底交付：`cat /input/case.json` 核对契约键与 case_id，手写合法但不完整的 result.json：

```json
{"case_id": "<从 case.json 原样复制>", "status": "partial",
 "result": {}, "quality": [], "descriptive": [], "inferential": [],
 "effects": [], "interpretation": {"note": "脚本未运行，仅手工骨架"},
 "sources": [{"source_id": "S1", "path": "/input/case.json", "kind": "text"}],
 "uncertainty": ["脚本未运行，仅手工兜底"], "change_log": [],
 "errors": ["script_unavailable: <实际错误>"], "degradations": ["hand-written fallback"],
 "pending_human": []}
```

写完自检：`python -c "import json;json.load(open('/app/output/result.json'))"`。**宁可 partial，不要零产物。**

## 8. 参考文件

- `references/methods.md`：检验选择表、效应量/CI 口径、七类情形、常见错误。

## 9. 版本与维护

- 1.1.0：纯标准库确定性统计库（Shapiro-Wilk Royston 近似、t/F/χ²/正态、Mann-Whitney/Kruskal-Wallis、效应量、CI）+ 清洗/描述/推断流水线 + 契约回填与端到端自检。
- 自检：`python scripts/stats.py`（统计库，退出码 0）、`python scripts/selftest.py`（端到端 + 确定性 + 降级，退出码 0）。
