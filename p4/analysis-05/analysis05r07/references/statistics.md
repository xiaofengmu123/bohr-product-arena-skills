# 统计口径与假设校验规则

本文件是 `scripts/core_impl.py` 中推断统计的口径说明，供 Agent 复核与引用。所有检验均为纯标准库实现，输出确定性（无随机数）。

## 1. 描述统计

- 数值列：`n / mean / std(sample, ddof=1) / sem / min / q1 / median / q3 / max / skewness / ci95`。
- 95% 置信区间用 t 分布临界值 `t_{0.975, n-1}`（小样本）而非固定 1.96。
- 分位数使用线性插值法（`q*(n-1)` 位置），与 `numpy.percentile` 的 `linear` 口径一致。

## 2. 检验选型决策树

| 组数 | 首选 | 对照（非参数） | 效应量 |
|---|---|---|---|
| 2 组 | Welch t（不假设方差齐） | Mann-Whitney U（秩） | Cohen's d（合并标准差） |
| ≥3 组 | 单因素 ANOVA | Kruskal-Wallis（秩） | η²（SS_between / SS_total） |

- 方差齐性以 **Levene 检验（中位数中心）** 评估；若 p < 0.05，报告中注明方差不等，Welch t 仍适用，ANOVA 结论附保留意见并给出 Kruskal-Wallis 对照。
- ≥3 组的两两比较用 **Welch t + Bonferroni 校正**，显著阈值为 `0.05 / 比较数`。
- 多重比较的原始 p 值全部保留在 `work/stats.json`，报告只标注是否通过校正阈值。

## 3. 前提检查口径

- **独立性**：默认每条记录独立（题目固定快照数据，无重复测量设计）；重复行去重后再检验。
- **样本量**：任一组长 < 2 时该列检验返回 `skipped`，不产出伪造 p 值。
- **常数列 / 零方差**：无法定义检验统计量，返回 `skipped` 或 `no_difference`，并写入 `skipped`。
- **分布形态**：以 skewness 作为描述性提示；不为正态性做高代价的 Shapiro-Wilk，改以非参数秩检验作为稳健对照（这是本变体“鲁棒优先”的设计取舍）。

## 4. 相关分析

- 仅对成对完整观测计算 Pearson r（listwise deletion，逐对 n 记录在案）。
- 显著性 `t = r·sqrt((n-2)/(1-r²))`，df = n-2，双侧。
- 相关矩阵按 |r| 降序排列，报告前 12 对；热图包含全部数值列对。

## 5. 特殊函数实现

- Γ：Lanczos（g=7, n=9）。
- 不完全 beta：连分式（Lentz 型）+ 对数预处理；用于 t / F 分布 CDF。
- 不完全 gamma P：级数 + 连分式（Numerical Recipes 形式）；用于 χ² 分布 CDF。
- 正态分位：Acklam 有理近似 + 一步 Halley 校正。
- 秩和检验：平均秩法处理结（tie），H 用结校正 `C = 1 - Σ(t³-t)/(N³-N)`。

## 6. 已知近似与限制

- 连续分布 CDF 的截断误差约 1e-12，对 0.0001 阈值级别结论无实质影响。
- Mann-Whitney U 用正态近似（含连续性校正）；样本极小时近似性下降，此时以 Welch t 为主结论。
- 报告中对 p < 0.0001 统一显示为 `<0.0001`，真实数值保留在 `stats.json`。
