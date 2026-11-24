# 统计口径与近似说明

本文件记录 `scripts/core_impl.py` 中各统计检验的公式口径与近似假设，供复核者判断结果边界。所有检验均为标准库纯手写实现，不依赖 scipy。

## 1. 描述统计

- 均值、样本标准差（n−1 分母）、中位数、Q1/Q3 用线性插值分位数（与 numpy 默认一致）。
- 异常值判定：IQR 1.5 栅栏，即 `[Q1−1.5·IQR, Q3+1.5·IQR]` 之外；**只标记不删除**。

## 2. 推断检验

| 检验 | 适用 | 统计量 | p 值近似 |
|---|---|---|---|
| Welch t | 2 组、方差不齐 | t，Satterthwaite 自由度 | 学生 t 分布 CDF（不完全 beta） |
| Student t | 2 组、方差齐 | t，n1+n2−2 自由度 | 同上 |
| Mann-Whitney U | 2 组、非参数 | U（取小者） | 正态近似 + 结值校正 |
| ANOVA F | ≥3 组 | F | F 分布 CDF（不完全 beta） |
| Kruskal-Wallis H | ≥3 组、非参数 | H，df=k−1 | χ² 分布 CDF + 结值校正 |
| Pearson r | 两连续变量 | r | t 变换后学生 t CDF |

效应量：Welch/Student t 报告 Cohen's d（以两组方差的均方根为分母）；ANOVA 报告 η²（组间平方和/总平方和）。

## 3. 特殊函数实现

- 正态 CDF：`math.erf`。
- 不完全 beta `I_x(a,b)`：Numerical Recipes 连分式（`betacf`/`betai`）。
- 不完全 gamma 正则化 `P(a,x)`：级数（`gser`）+ 连分式（`gcf`）。
- 学生 t CDF 由 `I_x(ν/2, 1/2)` 导出；F CDF 与 χ² CDF 分别由不完全 beta 与不完全 gamma 导出。

这些实现为通用数值算法，精度约 1e-12；在极端自由度（ν 或 df 极大、样本量极小）下可能逼近迭代上限，此时返回 `skipped` 而非伪 p 值。

## 4. 已声明的近似与未做事项（写入 uncertainty）

- 正态性未做 Shapiro-Wilk 等正式检验，仅作为假设记录。
- Mann-Whitney 的 p 值用正态近似，未做连续性校正。
- 卡方独立性检验未逐格验证期望频数 >5。
- 多重比较未做校正；若一个数据集上跑多个目标列，需在报告里提示家庭错误率风险。
- 单位与量纲差异仅能识别字段名/取值，无法自动换算；跨表单位冲突标记 `pending_human`。

## 5. 中间表示（IR）约定

| 文件 | schema | 职责 |
|---|---|---|
| `profile.json` | profile.v1 | 数据源发现与字段字典 |
| `plan.json` | plan.v1 | 执行计划：主表/分组列/目标列/方法选择理由/覆盖参数 |
| `clean_meta.json` | clean-meta.v1 | 清洗规则与行数影响 |
| `stats.json` | stats.v1 | 描述统计与推断检验的唯一数值来源 |
| `figure_model.json` | figure-model.v1 | 图形视觉编码模型，SVG 只由它渲染 |
| `manifest.json` | manifest.v1 | 各产物内容哈希与大小 |

规则：图形数值只准读 `stats.json`，报告数字只准读 `stats.json`，二者与 `figure_model.json` 冲突时以 `stats.json` 为准并记录。
