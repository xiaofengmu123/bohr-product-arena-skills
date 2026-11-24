# 统计口径与自检清单（stats_checklist）

本清单是 `scripts/main.py` 分析口径的权威说明，也是提交前逐条可勾选的自检表。
数字由脚本确定性产出；Agent 负责读数据字典、定设计、补写解释与不确定性。

## 1. 七类情形的处理口径

| 情形 | 处置 | 记录位置 |
|---|---|---|
| 缺失值 | 计数 + 比例，**不当 0**；仅删除测量列不可用的行 | `quality_checks` + `change_log` |
| 异常值 | IQR 1.5 标记，**不静默删除**，保留参与分析 | `quality_checks` + `change_log` |
| 重复记录 | 去重前后 n + 保留首条规则 | `change_log` |
| 统计假设 | 先正态性（Jarque-Bera）后方差齐性（Brown-Forsythe） | `inferential[].normality_*` |
| 图表选择 | 数值→直方图/箱线图 SVG；分类→频数表 | `figures/*.svg` |
| 代码运行 | 纯标准库、确定性、可复跑 | `analysis_code.py` / `scripts/` |
| 数据库字段 | SQLite 全表枚举 + 类型推断；单位不明标 `unit_unknown` | `schema.json` |

## 2. 描述统计口径

`n / mean / median / sd / min / max / Q1 / Q3 / IQR / skewness / kurtosis`。
- 分位数为线性插值（Hyndman-Fan type 7，numpy 默认）。
- `sd` 为样本标准差（n-1）；偏度/峰度用校正的 Fisher-Pearson g1、g2。
- 每个统计量带单位；字典未给单位时标 `unit_unknown`，**不猜 μg/mg、cm/mm**。

## 3. 检验选择表（脚本自动执行）

| 组数 | 正态 + 方差齐 | 正态 + 方差不齐 | 非正态/小样本 |
|---|---|---|---|
| 2 组 | Student t | Welch t | Mann-Whitney U（正态近似 + 结校正） |
| ≥3 组 | 单因素 ANOVA | Kruskal-Wallis（稳健兜底） | Kruskal-Wallis |

- **≥3 组禁止两两 t**；确需事后两两比较时用 Bonferroni 或 BH 校正，
  保留校正前后 p（脚本提供 `stats.bonferroni` / `stats.bh_fdr`）。
- 正态性用 Jarque-Bera（χ²(2)），n<20 时标记 `approximate` 且解释低功效；
  方差齐性用 Brown-Forsythe（Levene 中位数版）。
- 组内方差为 0 / n<2 / 缺失过高 → 该检验标"不适用"，**不给 p**，并说明影响。

## 4. 效应量与置信区间

| 设计 | 效应量 | CI |
|---|---|---|
| 两组均值 | Cohen's d / Hedges' g（方向+CI）；方差不齐补 Glass Δ | d 的正态近似 CI |
| ≥3 组 | eta² / omega²（方差解释比例） | —（非均值差 CI） |
| 分类 | Cramér's V | — |
| 相关 | Pearson r | Fisher z 变换 CI |
| 比例 | — | Wilson CI |
| 均值/均值差 | — | t CI / Welch CI |

- CI 是**参数覆盖区间**，不解释为"个体取值范围"。
- 效应量方向写清（`group1_minus_group2` 等），置信水平固定 0.95。

## 5. 结果解释纪律

- p 写精确值（`p=0.043`），不写"证明有效/证明无差异"。
- 区分统计显著与实际意义：结合效应量大小与 CI 宽度判断。
- 标注适用条件（正态/方差齐/样本量/单位/近似）、剩余不确定性与需人工确认项。

## 6. 提交前自检（逐条勾选）

- [ ] `python -B scripts/selftest.py` 退出码 0
- [ ] `result.json` 可解析，含 `case_id` + `output_contract` 全部字段 + `status/sources/uncertainty/change_log`
- [ ] 契约字段相对顺序与 `output_contract` 一致（`audit` 的 `contract_alignment` 全为 present）
- [ ] `status` 诚实：材料不足=`insufficient_evidence`，输入坏=`input_error`
- [ ] 缺失/异常/重复均有计数与处理规则（`quality_checks` + `change_log`）
- [ ] 检验方法与分布/设计匹配；先做正态性/方差齐性；≥3 组未用两两 t
- [ ] 每个关键数值有计算依据（`cleaned.csv` + 脚本 + 确定性，无 seed）
- [ ] 效应量与 95% CI 与检验、样本量、不确定性一致
- [ ] 同输入两次运行结果字节一致（无随机数、无墙上时钟）
- [ ] 图表为可编辑 SVG；代码可在 `/app/output` 复跑
- [ ] 无编造：不虚构数字、引用、软件状态、运行记录
