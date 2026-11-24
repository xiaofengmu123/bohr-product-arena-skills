# 统计方法与口径说明

本文件说明 Skill 内每个统计量的定义、适用条件、近似与不适用情况，便于复核与复用。全部用 Python 标准库从第一性原理实现，无第三方依赖。

## 1. 描述统计

- 均值 `sum/n`；标准差为样本标准差（`ddof=1`）；标准误 `SEM = SD/√n`。
- 中位数、Q1/Q3 采用线性插值分位数（`q*(n-1)` 位置），IQR = Q3 − Q1。
- 偏度使用 Fisher-Pearson 无偏校正；峰度为校正后的超额峰度（excess kurtosis）。
- 异常值只按 1.5×IQR 围栏**标记**（`[Q1−1.5·IQR, Q3+1.5·IQR]` 之外），记录数量与样本，**不删除**。

## 2. 分布与前提校验

- **正态性**：D'Agostino-Pearson 全向 K²（偏度 + 峰度联合，χ²(2)）。适用于 n≥8；n<8 时该检验返回不可用，组被保守视为非正态。偏度分量经 asinh 变换，峰度分量经 Anscombe-Glynn 三次方变换；实现已与 scipy `normaltest` 对拍一致。
- **方差齐性**：Levene 检验（中位数中心，即 Brown-Forsythe 变体），对非正态更稳健；每组建模为与组中位数的绝对偏差，做单因素 ANOVA。

## 3. 组间比较（按组数与前提选择）

| 组数 | 前提满足 | 主方法 | 统计量 |
|---|---|---|---|
| 2 | 两组正态 且 方差齐 | Student t（等方差） | t, df=n₁+n₂−2 |
| 2 | 两组正态 但 方差不齐 | Welch t | t, Satterthwaite df |
| 2 | 任一非正态 | Mann-Whitney U | U（含结校正的正态近似 z） |
| ≥3 | 各组正态 且 方差齐 | 单因素 ANOVA | F, df=(k−1, N−k) |
| ≥3 | 否则 | Kruskal-Wallis | H（含结校正，χ²(k−1)） |

- 显著性水平 α=0.05；前提阈值 p>0.05 视为“不拒绝正态/齐性”。
- **sensitivity**：无论主方法为何，同时报告参数与非参数两种结果，便于口径复核。
- **效应量**：t 检验 → Hedges' g（校正 Cohen's d）；Mann-Whitney → rank-biserial r；ANOVA → η²；Kruskal-Wallis → ε²。
- **事后两两比较**：仅当 k≥3 且 omnibus p<0.05 时进行；全部对子 p 值做 Bonferroni 校正（×m，m=k(k−1)/2），报告 p_raw 与 p_bonferroni。
- 近似与局限：Mann-Whitney/Kruskal-Wallis 的 p 值用大样本正态/χ² 近似（含结校正），小样本（总 n<20 或组 n<5）不保证精确；K² 在 n<8 不可用。

## 4. 相关分析

- **Pearson r**：p 值经 `t = r·√((n−2)/(1−r²))`、df=n−2 求得；|r|=1 时 p=0。
- **Spearman ρ**：对两列做平均秩（含结）后按 Pearson 计算。
- 只使用两列均非缺失的完整样本对，实际 n 记入结果。

## 5. 特殊函数实现

- 不完全 gamma（级数/连分式）→ χ² CDF。
- 不完全 beta（连分式）→ t、F CDF。
- 正态尾概率经 `math.erfc`。
- 上述实现均已与 scipy（Welch t、Mann-Whitney、ANOVA、Kruskal-Wallis、Levene、Pearson、normaltest）对拍一致。

## 6. 清洗口径

1. 精确重复行（全部单元格一致）删除，记录条数。
2. 全空行删除，记录条数。
3. 数值列按 ≥80% 可解析为数字判定，强制转 float；不可解析的单元格计数记录。
4. 缺失值仅在“分析变量”上做整例删除（listwise），原始单元格在 `cleaned.csv` 保留为空白，不静默填补。
5. `consistency_check = (删重复行 + 删全空行) == (原始行数 − 清洗后行数)` 必须为 true。

## 7. 确定性约定

- 无随机数；JSON 输出 `sort_keys=True`、紧凑分隔符；浮点 12 位有效数字规范化，非有限值置 null。
- `result.json.runtime` 是唯一墙钟字段，自检比较时剥离；其余字段与全部中间/最终产物两次运行字节一致。
