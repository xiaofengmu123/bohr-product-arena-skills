# 统计方法与证据溯源口径

本文件说明 `scripts/core_impl.py` 中统计检验的实现口径与前提，供复核；全部只用 Python 标准库，不依赖 numpy/scipy/pandas。

## 1. 特殊函数与分布（数值实现）

- 正态 CDF：`0.5*(1+erf(x/√2))`，用 `math.erf`。
- 正则化不完全 beta `I_x(a,b)`：连分式（Lentz 变体），前置因子用 `math.lgamma`。
- 正则化不完全 gamma `P(a,x)`：`x<a+1` 用级数，否则用连分式。
- t 分布 CDF：`I_{df/(df+t²)}(df/2, 1/2)` 关系式；双侧 p = `2*(1-CDF(|t|))`。
- F 分布 CDF：`I_{df1·F/(df1·F+df2)}(df1/2, df2/2)`。
- χ² 分布 CDF：`P(df/2, x/2)`。
- t 临界值（95% CI）：二分反演。

这些实现与 SciPy 在常规 df 与 p>1e-8 时一致；p 趋近 0 时受双精度浮点下溢限制，统一渲染为 `<1e-300` 并在 `uncertainty` 说明。

## 2. 描述统计

- 样本方差除以 `n-1`；标准差为样本标准差。
- 分位数用线性插值（numpy 默认 type 7 口径）：`pos=q*(n-1)`，落在两数之间线性插值。
- 偏度 G1 与超额峰度 G2 用偏差校正式（scipy.stats.skew/kurtosis 的 bias=False 口径）。
- 95% 置信区间用 t 临界值（`mean ± t_{0.975, n-1}·se`）。

## 3. 正态性 / 方差齐性

- Jarque-Bera：`JB = n/6·(G1² + (G2-3)²/4)`，χ²(2) 近似；`n<8` 或零方差时返回 skipped。
- Levene（Brown-Forsythe）：对“各观测值减本组中位数的绝对值”做 ANOVA，检验方差齐性。

## 4. 组间比较

- 两独立组：Welch t（不等方差，Satterthwaite df）+ Mann-Whitney U（正态近似 + 结校正）。
- 多组：单因素 ANOVA（F 与 η²）+ Kruskal-Wallis（H 与 χ²(k-1) 近似 + 结校正）。
- 事后两两比较：每组对同时给 Welch t 与 Mann-Whitney U（未做多重比较校正，报告原始 p 并在 `uncertainty` 提示）。
- 检验选择规则：方差齐（Levene p≥0.05）用 ANOVA，不齐用 Welch/Kruskal-Wallis；两者都报，不隐藏。

## 5. 相关分析

- Pearson：`r = sxy/√(sxx·syy)`，`t = r·√((n-2)/(1-r²))`，双侧 t(n-2)。
- Spearman：对两组秩做 Pearson。
- 成对删除缺失：每次检验只用两列均非缺失的行，`n` 记录成对完整样本数。

## 6. 清洗规则（默认，不静默丢数据）

1. `drop_exact_duplicates`：按全列归一化字符串完全相等判定，删除并记录行数。
2. `flag_outliers_iqr`：IQR×1.5 标记，仅记录行号与上下界，不删除。
3. `count_missing`：缺失 token 见 `MISSING_TOKENS`（空串、na、N/A、null、none、nan、`.`、`?`、`-`、`--`、`missing`），只计数不填补。

任何偏离默认规则的决策（填补、删除、winsorize）必须由 task.md 明确要求，且规则与行数影响写入 `clean_meta.json.rules`。

## 7. 证据溯源

- `result.json.sources[]` 每项含 `locator` + `quote`。
- 输入事实的 `quote` 是材料逐字片段（如表头行、task.md 原句）；推导结果的 `quote` 是 `key=value` 精确形式，`locator` 指向 `work/stats.json`。
- 数值、结构或操作结果保留可复核金标准：`cleaned.csv`、`clean_meta.json`、`profile.json`、`stats.json`、`discovery.json` 全部落盘。

## 8. 确定性保证

- 不引入随机数；所有集合/字典迭代前先排序；JSON 用 `sort_keys=True`。
- SVG 与 `stats.json` 只存相对文件名与数值，不存绝对路径或时间戳（时间戳仅在 `result.json.runtime/change_log`）。
