---
name: stat-analysis-02
description: 统计分析与结果解读（analysis-02）：对带缺失/异常/重复的科研数据完成清洗、描述统计、假设检验（先正态性/方差齐性）、效应量与置信区间，交付可复核的数字、图表、代码与证据；result.json 字段与 case.json 的 output_contract 逐一对齐并保留 change_log。仅在需要可复算、可追溯的统计结论时使用。
version: 2.0.0
---

# 统计分析与结果解读（stat-analysis-02）

把一份不干净的科研数据（实验记录、随访表、问卷导出、数据库表）处理到可统计状态，
给出描述统计、推断检验、效应量、置信区间与结果解释，**同时保留每一处计算依据**。
核心纪律：给出结果也保留依据；检验方法与数据分布/设计匹配；**不编造缺失软件状态或科学数据**。

## 1. 角色与何时使用

- **使用**：需要"可复算、可追溯"的清洗 + 描述统计 + 推断 + 效应量 + CI + 解释时。
- **不使用**：实验方案设计、样本量估算、论文写作；一次性脚本；无真实材料的纯问答。
- 本版侧重**输出契约完整**：`result.json` 字段从 `case.json` 的 `output_contract`
  逐键反射并对齐，`change_log` 全程记录每次清洗动作（见 `references/contract_notes.md`）。

## 2. 输入契约

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）。这是**权威契约**，字段名以它为准，不假设固定文件名/键名。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 其余材料：csv/tsv/json/sqlite(.db)/xlsx/docx/pdf 等；`environment/resources.yaml`
  声明可用工具（scientific-python、SQLite 等）。文本按 `utf-8-sig` 优先读取并容错。
- 工作目录 `/app/work`，**最终产物写 `/app/output`**。
- 预算：**64 次模型请求 / 900 秒 / 每次回复 16384 tokens**；不联网。

## 3. 输出契约（必读）

必须产出 `/app/output/result.json`，顶层 = `case_id` + `output_contract` **全部字段**
（逐键回填、缺值 `null`/`[]`、不改名不删未知键、保持契约原始相对顺序）+ 信封：

```text
case_id        # 从 case.json 原样复制，缺失写 "unknown-case"
status         # ok | partial | insufficient_evidence | input_error
sources[]      # {source_id, path, kind, rel_path}
uncertainty[]  # 不确定项与缺口
pending_human[]# 需人工确认项
change_log[]   # {id, time, action, before, after, reason}
errors[] / degradations[] / runtime{}   # 诊断与运行信息
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑，环境可渲染时补 PNG/PDF）、`quality_checks.json`、可复跑代码。

## 4. 执行流程（先骨架，后增量；900 秒预算）

**前 3 分钟内必须落盘一版合法 result.json 骨架，之后只做增量改进**——宁交不完整但
合法的结果，不交零产物。所有命令先 `cd /skills/weekly-skill` 再执行（不依赖 cwd）：

```bash
cd /skills/weekly-skill && python scripts/main.py skeleton   # ① ≤3min：契约骨架 + index.json
cd /skills/weekly-skill && python scripts/main.py survey     # ② 材料/工具清单
cd /skills/weekly-skill && python scripts/main.py inspect    # ③ 数据字典 + 每表 schema/类型
# ④ 读 task.md/case.json 确定分析设计（两组?三组?配对?相关?单位?），必要时写 plan.json
cd /skills/weekly-skill && python scripts/main.py analyze    # ⑤ 确定性清洗+描述+检验+效应量+CI
# ⑥ 依据设计补写 interpretation/uncertainty，写回 result.json（逐字段对齐契约）
cd /skills/weekly-skill && python scripts/main.py emit       # ⑦ 汇总 result.json + 主产物
cd /skills/weekly-skill && python scripts/main.py audit      # ⑧ 契约对齐 + JSON 自检
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① skeleton | /input | result.json 骨架 + /app/work/index.json | ≤3 分钟 |
| ②③ survey/inspect | /input | 材料清单 + schema.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | 分析计划（设计/单位/假设） | 若干次请求 |
| ⑤ analyze | /input + ④ | /app/work/analysis.json + cleaned CSV | 确定性，秒级 |
| ⑥ 解释补写 | ⑤ 结果 | result.json 的 interpretation/uncertainty | 剩余请求 |
| ⑦⑧ emit/audit | /app/work | /app/output 全部产物 + 校验 | 分钟级 |

`analyze` 自动选择数值列与分组列；Agent 在 ④ 判断语义后可用 `--plan plan.json`
显式指定 `{"measure_col": ..., "group_col": ...}` 覆盖自动推断。

## 5. 分析规范（对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明标 `unit_unknown`，不猜单位。
2. 清洗：缺失计数+比例（不当 0）；异常值 IQR 1.5 **标记不删除**；重复去重前后 n +
   保留首条。动作全进 `change_log` 与 `quality_checks.json`。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis；写口径与单位。
4. 检验前先做正态性（Jarque-Bera）与方差齐性（Brown-Forsythe）。选检验：正态+齐→
   Student t/ANOVA；正态+不齐→Welch；非正态/小样本→Mann-Whitney/Kruskal-Wallis；
   **≥3 组禁止两两 t**；多重比较 Bonferroni/BH 校正并保留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA eta²/omega²、
   分类 Cramér's V；写口径与 CI。
6. CI：关键估计给 95% CI（均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z）。
   CI 是参数覆盖区间，不误读为个体取值范围。
7. 解释：区分统计显著与实际意义；p 写精确值（`p=0.043`），不写"证明有效"；标注
   适用条件、剩余不确定性与需人工确认项。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] `result.json` 可解析，含 `case_id` + `output_contract` 全部字段 + status/sources/uncertainty/change_log
- [ ] 契约字段相对顺序与 `output_contract` 一致（`audit` 的 contract_alignment 全 present）
- [ ] `status` 诚实：材料不足=insufficient_evidence，输入坏=input_error
- [ ] 缺失/异常/重复七类情形都有明确计数与处理规则（quality_checks.json + change_log）
- [ ] 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] 每个关键数值有 locator+quote 证据或计算依据（cleaned.csv + 代码，无 seed）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行结果字节一致（无随机数、无墙上时钟）
- [ ] 图表为可编辑 SVG（环境可渲染时补 PNG/PDF）；代码可在 /app/output 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录
- [ ] 提交前 `python -B scripts/selftest.py` 退出码 0

## 7. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON/坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p；说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| xlsx/docx/pdf 等二进制 | stdlib 无法解析 → 记 degradation，不冒充解析结果 |
| scipy 不可用 | 用 `scripts/stats.py` 内置标准库实现（标注近似口径） |
| 脚本不可运行 | 手写合法 result.json（见 contract_notes §1），退出码恒 0 |

## 8. 脚本不可用时的保底交付

若 `cd /skills/weekly-skill` 失败、`python` 不可用或脚本报错，**不要交零产物**：
`cat /input/case.json` 核对契约键与 case_id，手写一份合法但不完整的 result.json——
顶层 = case_id + 契约字段（缺值 null/[]）+ `status:"partial"` + 信封键。写完自检
`python -c "import json;json.load(open('/app/output/result.json'))"`。宁可 partial，不要零产物。

## 9. 参考文件

- `references/contract_notes.md`：契约字段对齐、change_log 规范、status 语义、评分映射。
- `references/stats_checklist.md`：统计口径、检验选择表、效应量/CI、七类情形、常见错误。

## 10. 版本与维护

- 2.0.0：契约优先——result.json 从 output_contract 逐键反射并对齐，change_log 一等公民，
  去重/时钟去除随机与非确定因素，纯标准库确定性统计引擎 + selftest。
- 新增能力须对应评分维度；脚本内自检 `python scripts/selftest.py` 退出码 0。
