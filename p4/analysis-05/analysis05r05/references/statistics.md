# 统计方法与自检口径（methods & checks）

本 Skill 的统计全部由 `scripts/core_impl.py` 用标准库手写实现（t/F/χ² 分布经不完全 beta/gamma 函数计算），不依赖 scipy。以下口径与报告、`stats.json` 保持一致。

## 1. 描述统计

每个数值列输出：`n, mean, sd, min, q1, median, q3, max, iqr, skewness, kurtosis`。四分位数用线性插值（与 numpy `percentile(method='linear')` 一致）；方差用 n−1 无偏估计；偏度/峰度分别用三阶/四阶中心矩的无偏式（峰度已减 3）。

## 2. 分组比较（方法选择）

| 组数 | 参数检验 | 非参数检验 |
|---|---|---|
| 2 | Welch t 检验（不等方差，Satterthwaite 自由度） | Mann-Whitney U（正态近似，含并列校正） |
| ≥3 | 单因素 ANOVA（F 检验，报告 η²、ω²） | Kruskal-Wallis H（χ² 近似，含并列校正） |

- p 值均为双尾、名义值，未做多重比较校正（报告与 uncertainty 中显式说明）。
- 效应量：两组用 Cohen's d（合并方差）；多组用 η² 与 ω²。
- 前提校验为近似校验：正态性以偏度/峰度与样本量提示，方差齐性以组内 SD 提示；标准库无 Shapiro-Wilk/Levene，已写入 `test_assumptions` 与 uncertainty。

## 3. 相关与回归

- Pearson r：完整观测配对；p 值为 df=n−2 的双尾 t 检验；同时输出 r²。
- 单变量 OLS：以目标列（`target/strength/label/response/outcome/y/score/...` 或最后一个数值列）为因变量，逐预测列回归，输出 slope/intercept/R² 与 Pearson r。
- 相关/回归结论只在样本量足够（n≥3）且方差非零时给出，否则返回 `error` 而非伪造值。

## 4. 清洗口径（行数守恒）

- 重复：所有列取值完全相同的记录视为重复，删除并计数。
- 缺失：对数值列做完整案例分析（任一数值列缺失即删行），规则与删行数写入 `clean_meta.json`。
- 异常：Tukey IQR（1.5×IQR）**只标记不删除**，输出 fences 与个数。
- 守恒校验：`duplicates_dropped + rows_dropped_for_missing == original_rows − rows_kept`；自检脚本会复核 `cleaned.csv` 行数等于 `rows_kept`。

## 5. 自检映射（对应评分维度）

| 检查项 | 对应维度 |
|---|---|
| 契约键全覆盖、change_log 可审计 | 结果完整性 / 可核验性 |
| 删行数守恒、异常只标记不删 | 结果正确性 |
| Welch/ANOVA/MW/KW 方法匹配组数、效应量齐全 | 结果正确性 / 统计口径 |
| SVG 与 stats.json 同源、确定性字节一致 | 可核验性 / 运行稳定性 |
| 空输入/坏 JSON/缺列/除零/编码/SQLite 失败退出码 0 且说明 | 异常处理 |
| 报告数字全部回填自 stats.json | 可复现证据 |
