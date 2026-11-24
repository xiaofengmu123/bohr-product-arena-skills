# 统计口径与检查清单（stats_checklist）

本清单是 `SKILL.md` §5–§6 的展开，Agent 在 `analyze` 之后、`build` 之前逐条对照。

## 1. 检验选择表（先假设，后检验）

| 设计 | 条件 | 检验 | 效应量 | 95% CI |
|---|---|---|---|---|
| 两组独立 | 正态 + 方差齐 | Student t | Cohen d / Hedges g | 均值差 t CI / d CI |
| 两组独立 | 正态 + 方差不齐 | Welch t | Glass Δ / Hedges g | Welch 差 CI |
| 两组独立 | 非正态或 n<2 组 | Mann-Whitney U | rank-biserial 或 Cohen d | 不作参数 CI |
| 三组及以上 | 正态 + 方差齐 | 单因素 ANOVA | eta² / omega² | 组均值 t CI |
| 三组及以上 | 非正态 | Kruskal-Wallis H | eta²（η²） | 不作参数 CI |
| 配对/重复测量 | 正态差分 | 配对 t | dz（差值的 d） | 差值 t CI |
| 分类关联 | 期望频数足够 | 卡方 | Cramér's V | — |
| 两连续变量 | 线性 | Pearson r | r² | Fisher z CI |

- **≥3 组禁止两两 t**：先做整体检验，显著后再做多重比较（Bonferroni 或 BH），留校正前后 p。
- 正态性用 Shapiro-Wilk（Royston 1995 AS R94，`statlib.shapiro_wilk`，3≤n≤5000）。
- 方差齐性用 Brown-Forsythe（中位数中心化的 Levene，`statlib.brown_forsythe`）。
- 组内方差为 0、n<2、缺失比例过高时，该检验标"不适用"，不给 p，说明影响。

## 2. 效应量与 CI 的读法

- Cohen's d / Hedges' g：0.2 小、0.5 中、0.8 大（仅是约定，不替代领域判断）；方向由
  均值差决定，CI 跨越 0 表示与"无差异"相容。
- eta² / omega²：方差分析的解释比例；omega² 对自由度校正，小样本更保守。
- CI 是**参数覆盖区间**（重复抽样下覆盖真值的频率），不是个体取值范围，也不是"大概率区间"。
- 效应量 CI 与检验一致：p 显著但 d CI 宽且近 0，说明效应量估计精度低，应如实说明。

## 3. 七类情形的标准处置

| 情形 | 处置 | 记录位置 |
|---|---|---|
| 缺失值 | 计数 + 比例；**不当 0**；不静默整行删 | cleaning_model.json / quality_checks.json |
| 异常值 | IQR 1.5 标记；**不静默删除**；报告是否含异常值 | cleaning_model.json（flag_only） |
| 重复记录 | 去重前后 n + 保留规则（默认首现） | cleaning_model.json（D-1） |
| 统计假设 | 正态性/方差齐性先于检验；不符则换非参数 | stat_model.json（normal_p_group/bf） |
| 图表选择 | 分布→直方图/箱线图；组间→箱线图/均值±CI 条形图 | figure_model.json |
| 代码运行 | 标准库确定性；同一输入两次运行一致 | run_log.json（hash） |
| 数据库字段 | PRAGMA 读 schema；区分 numeric/categorical/text | data_model.json |

## 4. 常见错误（避免）

- 缺失值当 0 参与均值；整行静默删除不记录。
- 三组塞进两样本 t；多次两两比较不做校正。
- 非正态小样本仍用参数法；不看方差齐性。
- 报告只有 p 值，没有效应量与 CI；把 p=0.04 写成"证明有效"。
- CI 被解释成个体范围；单位 μg/mg、cm/mm 全程不交代。
- 随机数无种子、包版本不同导致不可复现。

## 5. 数字表达规范

- p 写精确值：`p=0.043`、`p<0.001`；统计量带自由度：`t(58)=2.03`、`F(2,147)=12.5`。
- 描述统计写口径与单位：`mean (sd)`、`median [Q1, Q3]`；单位不明写 `unit_unknown`。
- 每个关键结论给 `locator + 逐字 quote`（数据或中间产物中的原文片段）。
- 统计显著 ≠ 实际意义：显著与否是证据强度，效应量与 CI 才是"差多少、有多确定"。
