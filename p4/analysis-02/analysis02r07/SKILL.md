---
name: stat-analysis-02
description: 统计分析与结果解读（analysis-02，鲁棒性强化版）：对带缺失/异常/重复的科研数据完成确定性清洗、描述统计、假设检验（先正态性/方差齐性）、效应量与 95% 置信区间，交付可复核的数字、可编辑图表、代码与证据定位。以鲁棒性为首要设计目标：空输入、坏 JSON、缺参数、超大文件、缺软件均优雅降级而不崩溃。仅用于需要可复算、可追溯统计结论的场景；不负责实验设计、样本量估算与论文写作。
version: 2.0.0
---

# 统计分析与结果解读（stat-analysis-02，鲁棒性强化版）

输入是科研数据 + 数据字典 + 任务说明。产出：清洗表、描述统计、假设检验、效应量、
置信区间与结果解释，全部带证据定位与运行日志。核心纪律：**给出结果，也保留计算依据**；
检验方法与数据分布/设计匹配；**任何异常都不崩溃、不编造、不静默删改**。

## 1. 角色与何时使用

- **使用**：给定一份（或多份）科研表格/数据库，需要完成「清洗 → 描述 → 假设检验 →
  效应量 → 置信区间 → 解释」的可复算统计结论时。
- **不使用**：实验方案设计、样本量估算、论文写作；一次性临时计算（直接写脚本即可）。

## 2. 输入契约（先发现，再假设）

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）；这是**权威契约**，字段名以它为准。
- `/input/task.md`：题目说明与评分要求；必须实际读取，不依据标签脑补。
- `/input/` 材料：csv/tsv/json/sqlite(.db)/xlsx/xls/docx/pdf 等。PDF 只给原始文件，
  解析用真实 PDF 而非预提取层。
- 工作目录 `/app/work`，**最终产物写 `/app/output`**。
- 预算：64 次模型请求 / 900 秒 / 16384 tokens 每次回复；不联网；脚本只用标准库。

## 3. 输出契约（必读）

必须产出 `/app/output/result.json`。顶层 = `case_id` + `output_contract` **全部字段**
（逐键回填，缺值 `null`/`[]`，不改名、不删未知键；语义匹配的键填入实际结果）+ 信封：

```text
case_id         # 从 case.json 原样复制，缺失写 "unknown-case"
status          # ok | partial | insufficient_evidence | input_error
result{}        # 主结果：tables_analyzed/cleaning/descriptive/inferential/effects/confidence_intervals
descriptive[] / inferential[] / effects[] / interpretation{}   # 顶层镜像
sources[]       # {source_id, path, kind, rel_path}
uncertainty[] / pending_human[] / errors[] / degradations[] / change_log[] / runtime{}
```

主产物（可编辑、可复现）：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
（可编辑；环境可渲染时补 PNG/PDF）、`quality_checks.json`、`code/`（实际运行脚本快照）。

## 4. 执行流程（先骨架，后增量；总预算 900 秒 / 64 请求）

**前 3 分钟内必须先落盘一版 result.json 骨架（`init`），之后只做增量改进**，宁交不完整但
合法的结果，不零产物。脚本路径均为相对路径，先 `cd /skills/weekly-skill`（若目录不同先
`find / -maxdepth 4 -name SKILL.md` 定位）。

```bash
cd /skills/weekly-skill && python scripts/main.py init      # ① ≤3 分钟必做：索引 + partial 骨架
cd /skills/weekly-skill && python scripts/main.py survey    # ② 材料/工具清单（秒级）
cd /skills/weekly-skill && python scripts/main.py inspect   # ③ 每表 schema/列类型/缺失·重复·异常计数
# ④ 读 task.md/case.json 确定分析问题与设计（两组?三组?配对?相关?单位?），人工确认启发式设计
cd /skills/weekly-skill && python scripts/main.py analyze   # ⑤ 确定性清洗+描述+检验+效应量+CI（秒级）
# ⑥ 依据设计复核/补写解释与证据定位（locator+quote），必要时用 scipy 交叉核对 p 值
cd /skills/weekly-skill && python scripts/main.py build     # ⑦ 汇总 result.json + 全部主产物
cd /skills/weekly-skill && python scripts/main.py selfcheck # ⑧ 复检（退出码 0 才交付）
```

| 步 | 输入 | 输出 | 预算 |
|---|---|---|---|
| ① init | /input | /app/output/result.json 骨架 + /app/work/index.json | ≤3 分钟 |
| ②③ survey/inspect | /input | survey.json + schema.json | 约 1 分钟 |
| ④ 任务解读 | task.md/case.json | 分析计划（设计/单位/假设）+ 证据定位 | 若干请求 |
| ⑤ analyze | /input + ④ | /app/work/analysis.json + 统计结果 | 确定性，秒级 |
| ⑥ 解释补写 | ⑤ 结果 | result.json 的 interpretation/uncertainty/evidence | 剩余请求 |
| ⑦⑧ build/selfcheck | /app/work | /app/output 全部产物 + 校验 | 分钟级 |

## 5. 分析规范（硬要求，对应评分维度）

1. 先读数据字典/字段语义/单位；单位不明写 `unit_unknown`，不猜 μg/mg、cm/mm。
2. 清洗：缺失计数+比例（不当 0）；异常值 IQR 1.5 标记不静默删除；重复去重前后 n + 保留规则。
3. 描述统计：n/mean/median/sd/min/max/Q1/Q3/IQR/skewness/kurtosis，写口径与单位。
4. 先正态性（Jarque-Bera + Shapiro-Francia W′）与方差齐性（Brown-Forsythe），再选检验：
   正态+齐→Student t/ANOVA；正态+不齐→Welch；非正态/小样本→Mann-Whitney/Kruskal-Wallis；
   **≥3 组禁止两两 t**；多重比较必须校正（Bonferroni/BH）并留校正前后 p。
5. 效应量：两组 Cohen's d/Hedges' g（方向+CI）、方差不齐 Glass Δ、ANOVA eta²/omega²、
   分类 Cramér's V；写口径与置信区间。
6. 置信区间：均值 t CI、均值差 Welch CI、比例 Wilson、相关 Fisher z；不误读为个体范围。
7. 结果解释：区分统计显著与实际意义；p 写精确值，不写"证明有效"；标适用条件与不确定项。

## 6. 自检清单（逐条可勾选）

- [ ] result.json 可解析，含 case_id + output_contract 全部字段 + status/sources/uncertainty/change_log
- [ ] status 诚实：材料不足=insufficient_evidence，输入坏=input_error/partial，正常=ok
- [ ] 缺失/异常/重复七类情形都有明确计数与处理规则（quality_checks.json）
- [ ] 检验方法与分布/设计匹配；先正态性/方差齐性；≥3 组未用两两 t；多重比较已校正
- [ ] 每个关键数值有 locator+quote 证据或计算依据（cleaned.csv + code/ + 确定性 seed）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行结果一致（无随机数，确定性；sort_keys 稳定输出）
- [ ] 图表为可编辑 SVG；代码快照可在 /app/output/code/ 复跑
- [ ] 空输入/坏 JSON/缺参数/超大文件/缺软件均优雅降级，退出码 0，不崩溃、不编造

## 7. 降级策略（诚实优先，不编造）

| 场景 | 处置 |
|---|---|
| 空输入目录 | `insufficient_evidence` + uncertainty 列缺口，退出码 0 |
| 坏 JSON / 坏编码 | 多编码回退（utf-8-sig/utf-8/latin-1/cp1252）+ 记 errors/degradations，继续处理其余材料 |
| 缺参数/未知参数 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；未知参数忽略 |
| 超大文件 | 单文件 >64MB 跳过并记 degradation，表行数封顶 200k，不 OOM |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标 `not_applicable`，不给 p，说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| scipy/numpy 不可用 | 用 scripts/statlib.py 内置纯标准库近似（含自测），标口径 |
| 写盘失败 | 降级到最小合法 result.json，进程退出码恒 0 |

## 8. 鲁棒性设计（本版与参考包的差异点）

- **鲁棒性优先**：所有读文件走 `read_bytes_safe/read_text_safe/read_json_safe`（大小上限、
  编码回退、坏 JSON 前缀抢救）；空行自动剔除；整包入口 `core_impl.run` 顶层 catch-all
  保证任何异常都写出合法 result.json 且退出码 0。
- **纯标准库确定性统计**：无 numpy/scipy 依赖；t/F/χ² 分布 p 值由不完全 Beta/Gamma
  函数自算，可与 scipy 交叉核对；无随机数，`sort_keys` 稳定输出，重复运行结果一致。
- **契约自适应回填**：output_contract 逐键回填，并通过别名映射（effect_size/confidence_interval
  等）填入实际结果，未知键保留 null/[]。
- **证据可复核**：clean 表 + code/ 快照 + quality_checks.json + 数字来源共同构成可复算链。

## 9. 参考文件

- `references/stats_checklist.md`：统计口径、检验选择、效应量/CI 解释、七类情形、常见错误。
- `references/robustness_notes.md`：鲁棒性规则、降级矩阵、确定性复现要求。

## 10. 版本与维护

- 2.0.0：鲁棒性强化版——空输入/坏 JSON/缺参数/超大文件优雅降级；纯标准库统计 + 自测；
  契约自适应回填。脚本内自检 `python scripts/main.py selftest` 与 `selfcheck` 退出码 0。
