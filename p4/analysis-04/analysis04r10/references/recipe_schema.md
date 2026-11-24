# 声明式分析配方（analysis_recipe.json）规范

引擎不依赖 Agent 手写“一次性 Python”。Agent 把清洗、统计、查询、绘图、校验写成**类型化的声明式配方**，引擎确定性解释执行，并把每一步的行数变化、强制类型转换、SQL、图元与校验都写入 `state.json`（中间表示）。同一份配方在不同机器/不同 Python 版本上重跑得到相同结果（种子固定、字典按稳定顺序处理）。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `seed` | int | 随机种子，默认 42；当前引擎为纯确定性计算，种子仅用于可复现标记 |
| `source` / `input_file` | str | 指定主表格材料（manifest 中的 `rel` 或文件名）；缺省取第一个表格材料 |
| `cleaning` | list | 按顺序执行的清洗操作 |
| `statistics` | list | 统计规格 |
| `plots` | list | 绘图规格（SVG + PNG 预览） |
| `queries` | list | SQLite 查询规格 |
| `checks` | list | 显式校验断言 |

## cleaning（清洗操作，逐个 `{op, reason, ...}`）

- `coerce`：`{column, to: "int"|"float"|"str", errors: "coerce"|"raise"}`。**不把缺失静默当 0**：空串转 `None`。
- `rename`：`{column, to}`。
- `drop_na`：`{subset?, how: "any"|"all"}`，记录删除前后行数。
- `fill_na`：`{column, strategy: "mean"|"median"|"constant", value?}`，记录填充个数与填充值；**默认禁止隐式填 0**。
- `drop_duplicates`：`{subset?, keep: "first"|"last"}`，去重必须在分组统计**之前**。
- `filter`：`{column, op: "=="|"!="|">"|">="|"<"|"<="|"in", value}`。
- `select` / `drop`：按列保留/删除。

## statistics（统计规格，`{kind, output_key, ...}`）

- `describe`：数值列汇总（count/mean/median/std/min/max/q1/q3/skew/kurtosis），`columns?` 缺省取全部数值列。
- `mean` / `median` / `std` / `min` / `max` / `count`：单列，`{column}`。
- `groupby`：`{key, value, agg: "mean"|"median"|"sum"|"count"|"std"}`，分组内先排除缺失再聚合，并报告 `n` 与 `n_missing`。
- `correlation`：数值列两两 Pearson 相关系数。
- `ttest`：`{column, group_column, group_a, group_b}` 或 `{a_column, b_column}`。使用 Welch t（不等方差）双尾检验，`p_two_tailed` 由正规化不完全 beta 函数（连分数）计算；**同时明确写出假设未检验的警示**，不把 p 值当作默认可信结论。

## plots（绘图规格，`{kind, title, xlabel, ylabel, output, ...}`）

- `histogram`：`{column, bins?}`。
- `scatter`：`{x, y, label?}`。
- `bar`：`{x, y, agg?}`，x 为分组键，y 为数值列（缺省按 mean 聚合）。
- `box`：`{column, group_column?}`，含四分位、须线、离群点。

每个图**同源生成两份**：可编辑 `SVG`（轴、单位、图例、标题为权威标注）+ 真实 `PNG` 预览（stdlib `zlib` 写出，仅栅格化几何与数字刻度；字母标签以 SVG 为准）。文件名由 `output` 指定，缺省 `figure_<i>.svg`。

## queries（SQLite 查询规格，`{sql, db?, output_key}`）

引擎以只读模式打开 `.sqlite/.db/.sqlite3`，执行 SQL 并记录列名、行数、全部结果行与 SQL 原文。**WHERE 条件必须写全**；查询结果行数与预期不符时在 `checks` 中显式断言，而不是静默掩盖。

## checks（显式校验断言，`{kind, ...}`）

- `row_count_equals` / `row_count_le`：清洗后行数断言。
- `column_exists`：字段存在性。
- `no_missing`：指定列无缺失。
- `value_range`：结果均值落在给定区间。
- `mean_close`：`{output_key, expected, tol}` 与金标准/预期值比较。
- `status_allowed`：状态值合法。

任何断言失败都会被写入 `checks` 与 `state`，并在交付时体现为 `status=partial` 或显式 `errors`，绝不静默通过。

## 中间表示（state.json）要点

`<work>/state.json` 是所有子命令共享的唯一中间表示，包含 `manifest`（输入扫描）、`data_profile`（字段画像与类型）、`alignment`（任务字段→数据列对齐表）、`recipe`、`results`、`checks`、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`。增量执行时任何一步都可从 `state.json` 重建，不依赖 stdout。
