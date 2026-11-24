# 统计口径与检验选择清单（stats_checklist）

本文件是 SKILL.md 第 4 节「分析规范」的细则。脚本 `scripts/statlib.py` 与
`scripts/core_impl.py` 按此口径实现，所有数值可复核。

## 1. 七类必查情形（对应评分维度「结果完整性」）

| # | 情形 | 检查口径 | 处置规则（不静默） |
|---|---|---|---|
| 1 | 缺失值 | 每列计数 + 比例 | 不当作 0；describe 的 n 只算非缺失；`n_missing`/`pct` 记入 quality_checks |
| 2 | 异常值 | IQR×1.5 标记 | 只标记不删除；bounds + row_index + 条数进 quality_checks 与 limitations |
| 3 | 重复记录 | 全字段完全重复 | 去重保留第一条；去重前后 n + 规则进 change_log |
| 4 | 统计假设 | Shapiro-Wilk 正态性 + Brown-Forsythe 方差齐性 | 先假设后选检验；每检验带 method_note |
| 5 | 图表选择 | 组比较→分面箱线图；相关→热图 | 输出可编辑 SVG（环境可渲染时补 PNG/PDF） |
| 6 | 代码运行 | 标准库 + 确定性 | 无随机种子依赖；同一输入两次运行字节一致 |
| 7 | 数据库字段 | schema + 每列类型推断 | numeric/categorical/datetime/text 显式标注；单位不明写 unit_unknown |

## 2. 检验选择表

| 设计 | 正态 + 方差齐 | 正态 + 方差不齐 | 非正态 / 小样本 |
|---|---|---|---|
| 两组独立 | Student t | Welch t | Mann-Whitney U |
| 两组配对 | paired t | paired t（稳健性提示） | Wilcoxon（若实现）/标注降级 |
| ≥3 组 | 单因素 ANOVA | Welch ANOVA（近似，标注） | Kruskal-Wallis H |
| 两数值列 | Pearson r | Pearson r（稳健性提示） | Spearman rho |

禁止：≥3 组用两两 t 作为主检验。多重比较必须校正（Bonferroni 或 BH），
`p_raw` 与 `p_adj` 同时保留。

## 3. 效应量与 CI 口径

| 设计 | 效应量 | CI |
|---|---|---|
| 两组 | Cohen's d / Hedges' g（方向 + CI）；方差不齐补 Glass Δ | 均值 t CI、均值差 Welch CI |
| ≥3 组 | eta² / omega² | 各组均值 t CI |
| 分类关联 | Cramér's V | Wilson 比例 CI |
| 相关 | Pearson r（r² 为解释比例） | Fisher z 区间 |

CI 是参数覆盖区间，不解释为个体取值范围。

## 4. 结果解释规范

- p 写精确值（`p=0.043` 或 `p<0.000001`），不写「证明有效」「显著有效」。
- 区分统计显著与实际意义：显著 ≠ 大效应；报告效应量与 CI。
- 标注适用条件、剩余不确定性与 `pending_human`（单位确认、异常值是否真实等）。

## 5. 常见错误（自检时逐条否决）

1. 缺失当 0；2. 整行删除不说删多少；3. 异常值直接进均值；4. 重复未去重；
5. 三组套两样本 t；6. 不做正态/齐性直接套参数法；7. 只报 p 无效应量/CI；
8. p=0.04 写「证明有效」；9. 多重比较不校正；10. 依赖非标准库导致换机报错；
11. 单位 μg/mg、cm/mm 靠猜；12. 数字不可追溯、无证据定位。

## 6. 确定性要求

- 脚本无 `random`、无时间戳、无环境依赖排序；排序键固定。
- 同一输入 + 同一 plan.json → 两次 result.json 字节一致。
- p 值近似来源写 `method_note`；有 scipy 时用 `crosscheck` 复核并记录差异。
