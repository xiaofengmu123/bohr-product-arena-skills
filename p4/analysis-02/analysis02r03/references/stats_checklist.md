# 统计口径与检查清单（stat-analysis-02）

本文件是 SKILL.md §4 的展开：统计量口径、检验选择表、效应量与 CI、七类情形与常见错误。脚本
`scripts/core_impl.py` 的确定性实现与本表一一对应；p 值若用 scipy 交叉核对，须同时记录两个引擎与结果。

## 1. 描述统计口径

| 统计量 | 口径 | 说明 |
|---|---|---|
| n | 该变量**完整个案数**（缺失不算 0，也不静默整行删） | 报告 n_raw 与 n_used |
| mean / sd | 样本均值；样本标准差（分母 n−1） | sd=0 时相关检验标"不适用" |
| median / Q1 / Q3 / IQR | 线性插值分位数（type-7）；IQR=Q3−Q1 | 用于箱线图与 IQR 1.5 异常判定 |
| skewness | 调整 Fisher–Pearson G1 | \|G1\| 大提示偏态 |
| kurtosis | 超额峰度 G2（excess） | 与正态的 0 比较 |

## 2. 清洗规则（七类情形前 3 类）

- **缺失**：逐列计数 + 比例，写进 quality_checks.json；不当 0、不静默删；检验用 listwise（每检验只删该检验用到的缺失对），报告 n_used。
- **异常**：IQR×1.5 标记（lower/upper + 逐条 flags），**不静默删除**；描述统计同时给原始与稳健口径。
- **重复**：按 id 列（缺省全列）判重；去重前后 n + 保留规则（keep_first）+ 被删行 index 全记录。

## 3. 假设检验选择表（先正态性、再方差齐性）

| 设计 | 条件 | 检验 |
|---|---|---|
| 两组独立 | 正态 + 方差齐 | Student t（pooled） |
| 两组独立 | 正态 + 方差不齐 | Welch t |
| 两组独立 | 非正态 / 小样本 / 明显偏态 | Mann–Whitney U |
| 配对两组 | 差值正态 | Paired t |
| ≥3 组独立 | 正态 + 方差齐 | One-way ANOVA + 事后两两（校正） |
| ≥3 组独立 | 非正态 / 方差不齐 | Kruskal–Wallis + 事后两两（校正） |
| 两连续变量 | 双变量正态 | Pearson r；否则 Spearman ρ |

- 正态性：scipy 可用用 `shapiro`；否则 `shapiro_wilk_francia`（Blom 分数 W' + Royston 近似，口径同 R `nortest::sf.test`）。样本量 <5 或组内方差为 0 → 该检验标"不适用"。
- 方差齐性：Brown–Forsythe（中位数绝对偏差 ANOVA）。
- **≥3 组禁止两两 t**；事后比较给 raw + Bonferroni + BH（Benjamini–Hochberg）三种 p，写校正口径。

## 4. 效应量与置信区间

| 目标 | 指标 | 方向/口径 |
|---|---|---|
| 两组均值差 | Cohen's d / Hedges' g | g=d×J（小样本校正 J=1−3/(4N−9)）；方向=组1−组2，附 95% CI |
| 方差不齐 | Glass Δ | 分母=参照组 sd（写明 reference） |
| ≥3 组方差分析 | eta² / omega² | omega² 对 eta² 做偏置校正 |
| 分类关联 | Cramér's V | sqrt(χ²/(N·min(r−1,c−1))) |
| 均值 | t CI | df=n−1，95% |
| 均值差 | Welch CI | Welch–Satterthwaite df，95% |
| 比例 | Wilson 区间 | 小样本/近边界稳健 |
| 相关系数 | Fisher z 变换 CI | se=1/sqrt(n−3) |

CI 是**参数覆盖区间**，不误读为个体取值范围；区间方向与检验方向、样本量一致。

## 5. 结果解释纪律

- p 写精确值（`p=0.043`），不写"证明有效/显著即重要"；区分统计显著与实际意义（看效应量与 CI 宽度）。
- 每个关键数值给 locator + 逐字 quote 或计算依据（cleaned.csv + analysis_code.py + seed/fingerprint）。
- 标注适用条件（正态/齐性是否成立）、剩余不确定性、需人工确认项（单位、设计、离群值的领域判断）。

## 6. 七类情形 → 交付字段映射

| 情形 | 交付位置 |
|---|---|
| 缺失值 | quality_checks.json.missing + change_log |
| 异常值 | quality_checks.json.outliers + figures 箱线图离群点 |
| 重复记录 | quality_checks.json.n_duplicates_dropped + duplicate_dropped |
| 统计假设 | result.json.inferential[*].decision_basis / normality / variance_equality |
| 图表选择 | figures/*.svg（分组→箱线图；单变量→直方图） |
| 代码运行 | analysis_code.py + runtime.python_version + engine |
| 数据库字段 | schema.json 列类型/单位/基数；单位不明=unit_unknown |

## 7. 常见错误（必须避免）

1. 缺失当 0、整行静默删除不报数。
2. 异常值直接进均值、或静默删除。
3. 重复不去重导致 n 虚大。
4. ≥3 组用两两 t、多重比较不校正。
5. 不做正态性/方差齐性就套参数检验。
6. 只报 p 不报效应量与 CI；p<0.05 写"证明有效"。
7. 脚本依赖包版本、含随机数、依赖 cwd；换机器跑不出同一组数。
8. 单位 μg/mg、cm/mm 全凭猜，不标 unit_unknown。
