# 统计选型与口径规则

本文档规定清洗、统计检验、可视化与数据库查询的默认口径，供 Agent 与复核者对照 `stats.json` 与 `clean_meta.json` 使用。

## 1. 数据导入与字段对齐

- 扫描 `/input` 下 `.csv/.tsv/.txt/.json/.db/.sqlite/.sqlite3`，跳过 `case.json`/`task.md` 与已知产物文件名。
- CSV/TSV 用 `csv.Sniffer` 自动识别分隔符与引号；JSON 支持记录数组、`data/rows/records` 键下的记录数组、以及"列名→数组"的列式结构。
- SQLite 以 `file:...?mode=ro` 只读打开，用 `sqlite_master` 枚举表、`PRAGMA table_info` 读结构、`COUNT(*)` 读数；仅允许 `SELECT` 与 `PRAGMA` 语句。
- 字段名规范化 `_norm_key`（小写、非字母数字转下划线、去重加后缀），原始名保留在 `profile.json` 的 `raw_name`，形成可核对的字段映射。
- 类型推断：全非空值可转数值 → numeric；否则 categorical；全空 → empty。角色推断另按基数判断：唯一值 2–15 的列为分组候选；"id/index/row/no/code" 且全唯一的列从数值分析中剔除。

## 2. 清洗与异常处理

- **重复**：按"规范化后全列值完全一致"判定并删除，逐行计数；`clean_meta.consistency.removed_rows` 必须等于 `rows_before − rows_after`。
- **异常值**：数值列按 1.5×IQR 判定，**只标记不删除**，记录 `{row_index, column, value, rule}`。
- **缺失**：按列计数，默认**不插补**；统计阶段逐检验 listwise（成对完整），并在 `uncertainty` 中说明每列缺失数。
- 类型不符/全空列：记 `degradations` 后跳过该列，不中断整体流程。

## 3. 描述统计与正态性

- 每数值列：n、缺失数、均值、样本标准差（ddof=1）、min/q1/median/q3/max、偏度、峰度（Fisher-Pearson 调整）。
- 正态性：有 scipy 用 Shapiro-Wilk；无 scipy 或 Shapiro 返回非有限值时用 D'Agostino-Pearson K²（χ²(2)）。方法名与样本量写入 `stats.normality`。

## 4. 推断统计选型

| 结构 | 参数方法 | 非参数方法 | 效应量 |
|---|---|---|---|
| 两组（数值结局） | Welch t（不等方差） | Mann-Whitney U | Cohen's d |
| ≥3 组（数值结局） | 单因素 ANOVA | Kruskal-Wallis | η² / η²_H |
| 两个数值变量 | Pearson r | Spearman ρ | r / ρ |
| 两个分类变量 | χ² 独立性 | — | Cramér 缺失时记 NA |

- 前提校验：独立性按研究设计默认成立；方差齐性用 Welch 校正规避；正态性结果写入报告；样本量不足（组内 <2）返回 `skipped`。
- 多重比较：所有组间检验的 p 值做 Bonferroni 校正（`p_bonferroni`），同时保留原始 p；`stats._multiple_comparison` 记录方法与检验总数。
- 回归：若识别到数值结局列（名称含 target/strength/label/response/outcome/y/score，词边界匹配），对每个其他数值列做简单 OLS（斜率/截距/R²/p），并做多元 OLS（正规方程 + 高斯消元），注明"无共线性诊断"。

## 5. p 值的标准库实现（无 scipy 时）

- 正态 CDF 用 `math.erf`；正态分位数用 Acklam 逆正态近似。
- 对数伽马用 Lanczos（g=6）近似；t/F 的尾概率经不完全 beta 连分式；χ² 尾概率经不完全 gamma 级数/连分式。
- 这些实现是**近似**，写入 `uncertainty` 并在 `stats.backend.scipy=false` 时明确标注；有 scipy 时优先用 scipy 并记录 `backend.scipy=true`。

## 6. 可视化与报告

- 图型按数据形态选择：分组均值柱状图（均值±SEM）、分组合箱图（IQR 箱、1.5×IQR 须）、相关矩阵热图、最强相关散点（标注 r 与 p）。
- SVG 全部由 `stats.json` 程序化生成，无手写数字；轴标签注明单位与误差含义。
- 报告 `report.md` 的每个数字均从 `stats.json`/`clean_meta.json` 回填，可回算、可更新。

## 7. 失败与降级

- 空输入目录 → `insufficient_evidence`；坏 JSON → `input_error`；数据无法提取数值 → `insufficient_evidence`。
- 任何检验失败只标 `skipped/error`，绝不产出伪造 p 值；缺失软件/数据写 `pending_human` 或 `not_reported`。
