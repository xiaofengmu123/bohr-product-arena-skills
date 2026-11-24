---
name: analysis-02-rubric
description: 统计分析与结果解读（analysis-02）。对带缺失、异常、重复的科研数据完成确定性清洗、描述统计、假设检验（先正态性/方差齐性）、效应量与置信区间，并按评分维度逐条对齐交付可复核的 result.json、图表、代码与证据。仅在需要可复算、可追溯的统计结论时使用；不负责实验设计、样本量估算与论文写作。
version: 2.0.0
---

# 统计分析与结果解读（analysis-02-rubric）

输入是科研数据 + 数据字典 + 任务说明。产出：清洗表、描述统计、假设检验、效应量、
置信区间与结果解释，全部带证据定位与运行日志。核心纪律：**给出结果，也保留计算依据**；
检验方法与数据分布/设计匹配；不编造缺失软件状态或科学数据。

## 0. 安装位置与命令约定

本 Skill 由评测器安装在 `/skills/weekly-skill`；**所有命令先 `cd /skills/weekly-skill`
再执行**，脚本调用一律用相对路径 `scripts/xxx.py`，不依赖 cwd 之外的绝对路径。若路径
不确定先定位：

```bash
ls -d /skills/* 2>/dev/null; find / -maxdepth 4 -name SKILL.md 2>/dev/null | head
```

三个脚本：`scripts/main.py`（薄入口）、`scripts/core_impl.py`（实现）、
`scripts/selftest.py`（自检）。先自检：`python scripts/selftest.py`（退出码 0）。

## 1. 输入契约

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）；**这是权威契约**，字段名以它为准，逐键回填。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 材料：csv/tsv/json/sqlite(.db)/xlsx/docx/pdf 等；`environment/resources.yaml`
  声明可用工具（scientific-python、SQLite 等）。
- 工作目录 `/app/work`，**最终产物写 `/app/output`**。
- 预算：64 次模型请求 / 900 秒 / 每次回复 16384 tokens；不联网；脚本只依赖标准库。

## 2. 输出契约（必读）

必须产出 `/app/output/result.json`：顶层 = `case_id` + `output_contract` **全部字段**
（逐键回填，缺值 `null`/`[]`，不改名、不删未知键）+ 信封：

```text
case_id           # 从 case.json 原样复制，缺失写 "unknown-case"
status            # ok | partial | insufficient_evidence | input_error
sources[]         # {source_id, path, kind, rel_path}
uncertainty[]     # 不确定项与缺口
change_log[]      # {id, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑）、`quality_checks.json`、`analysis_code.py`、`core_impl.py`（复跑用）。

## 3. 预算与先落盘（硬约束）

总预算 900 秒、64 次模型请求。**前 3 分钟内必须先落盘一版 result.json 骨架
（`init`），之后只做增量改进**；宁交不完整但合法的结果，不零产物。

## 4. 执行流程（分步，每步输入→输出→预算）

```bash
cd /skills/weekly-skill && python scripts/main.py init        # ① 索引 + partial 骨架（≤3 分钟必做）
cd /skills/weekly-skill && python scripts/main.py survey      # ② 材料/工具清单
cd /skills/weekly-skill && python scripts/main.py inspect     # ③ 数据字典 + 每表 schema/列类型/单位
# ④ 读 task.md/case.json 确定设计（两组?三组?配对?相关?单位?），写 work/plan.json
cd /skills/weekly-skill && python scripts/main.py analyze     # ⑤ 确定性清洗+描述+检验+效应量+CI
# ⑥ 依据设计复核/补写解释（可用 scipy 交叉核对 p 值），更新 work/plan.json 或 result.json
cd /skills/weekly-skill && python scripts/main.py build       # ⑦ 汇总 result.json + 主产物
cd /skills/weekly-skill && python scripts/main.py selfcheck   # ⑧ 复检（含确定性比对）
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | /app/output/result.json 骨架 + /app/work/index.json | ≤3 分钟 |
| ②③ survey/inspect | /input | 材料清单 + schema.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | work/plan.json（设计/列/单位/假设） | 若干次请求 |
| ⑤ analyze | /input + plan | work/analysis.json + cleaned CSV + figures | 确定性，秒级 |
| ⑥ 解释补写 | ⑤ 结果 | result.json 的 interpretation/uncertainty | 剩余请求 |
| ⑦⑧ build/selfcheck | work | /app/output 全部产物 + 校验 | 分钟级 |

## 5. 评分维度 → 流程 → 自检 逐条映射（本版设计取舍）

本版以“评分维度对齐”为核心：每条评分维度都被映射到上表流程与第 7 节自检项，
自检项编号即对应维度，保证**评分点不落空**。

| 评分维度 | 分值 | 落到流程 | 对应自检 |
|---|---|---|---|
| 实际效果·任务完成度 | 15 | ⑤⑥ | A1、A3 |
| 实际效果·结果正确性 | 15 | ⑤⑥ | A4、A5 |
| 实际效果·结果完整性 | 5 | ⑤⑦ | A2、A6 |
| 实际效果·结果可核验性 | 5 | ⑦⑧ | A7、A8 |
| 使用增益·质量/效率 | 15+5 | ⑤（自动选检验、效应量绑定） | A4、B1 |
| 创新增益·新增能力 | 15 | ⑤（自动选检验、单位不明标注） | B1、B2 |
| 可靠体验·运行稳定/异常 | 4+3 | ①⑧ + 降级策略 | C1、C2 |
| 生态贡献·文档/复用 | 3+4 | SKILL.md + references | D1、D2 |

## 6. 分析规范（硬要求，对应正确性评分）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失（计数+比例，不当 0）；异常值（IQR 1.5 标记，不静默删除）；重复
   （去重前后 n + 保留规则）。动作全进 change_log 与 quality_checks.json。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis；写口径与单位。
4. 假设检验前先做正态性（Shapiro-Wilk）与方差齐性（Brown-Forsythe）。选检验：
   正态+齐→Student t/ANOVA；正态+不齐→Welch；非正态/小样本→Mann-Whitney/Kruskal-Wallis；
   **≥3 组禁止两两 t**；多重比较用 Bonferroni 或 BH 校正并留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA eta²/omega²、
   分类 Cramér's V；写口径与置信区间。
6. 置信区间：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z）。
7. 结果解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写“证明有效”；
   标注适用条件、剩余不确定性与需人工确认项。
8. 证据溯源：每个关键结论给 `{id, claim, locator, quote, basis}`；locator 指向
   文件#列，quote 为逐字数值/规则，冲突信息标 `source_conflict`。

## 7. 自检清单（逐条对应第 5 节编号）

- [ ] A1 缺失/异常/重复七类情形都有明确计数与处理规则（quality_checks.json）
- [ ] A2 result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] A3 按题目要求完成描述统计、推断统计、效应量与 CI，覆盖七类关键字段
- [ ] A4 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] A5 统计量、自由度、p 值、效应量、CI 数值正确；单位与有效数字符合口径
- [ ] A6 每个关键数值有 locator+quote 证据或计算依据（cleaned.csv + 代码）
- [ ] A7 同输入两次运行结果一致（无随机数，确定性）；`selfcheck` 已比对
- [ ] A8 图表为可编辑 SVG；代码可在 /app/output 复跑（analysis_code.py + core_impl.py）
- [ ] B1 按数据分布与设计自动选择检验；效应量与区间绑定到具体结论
- [ ] B2 字段口径不明时标 unit_unknown/pending_human，不硬算
- [ ] C1 status 诚实：材料不足=insufficient_evidence，输入坏=input_error，脚本坏=partial
- [ ] C2 样本量不足/组内方差为 0/缺失过高/假设不成立时该检验标“不适用”，不给 p
- [ ] D1 文档齐全：用途、边界、依赖、调用、失败处理、自查方法
- [ ] D2 无编造：不虚构数字、引用、软件状态、运行记录

## 8. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口，不编造数据 |
| 坏 JSON/坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标“不适用”，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| scipy/pandas 不可用 | 用脚本内置标准库实现（Shapiro-Wilk、t/χ²/F 分布），标口径 |
| 脚本不可运行 | 手写合法 result.json（见下），退出码恒 0 |

## 9. 脚本不可用时的保底交付

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

## 10. 参考文件

- `references/rubric_map.md`：评分维度逐条映射、流程/自检对应、复现与降级规则。
- `references/stats_checklist.md`：统计口径、检验选择表、效应量/CI 解释、七类情形、常见错误。

## 11. 版本与维护

- 2.0.0 本版：评分维度逐条对齐 + 标准库 Shapiro-Wilk/t-χ²-F 分布实现 + 确定性流水线
  + 证据溯源与自检；脚本 `python scripts/selftest.py` 退出码 0 为交付前提。
- 新增能力须对应评分维度；改动后重跑 selftest 与 selfcheck。
