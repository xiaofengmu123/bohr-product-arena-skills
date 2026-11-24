# 静默错误清单与数据字典对齐规则

本文件是 P3 复核阶段的逐项检查表，也是 `scripts/main.py profile` 输出的解读说明。
目标：拦截“代码跑通了但算错了”的静默错误。每一项都给出**检测方法**与**纠正动作**，
不允许凭感觉放过。

## 1. 缺失值与哨兵值（先“标记”，不静默“当作 0”）

- 检测：`data_profile.json` 每张表的 `missing_by_column`、`suspect_sentinel_by_column`。
- 常见缺失：空串、`NA`/`n/a`/`NaN`/`null`/`None`/`?`/`.`。
- 常见哨兵：`-999`/`-9999`/`9999`（仪器导出常用），`profile` 只**标记计数**，不自动删除，
  因为哨兵值也可能是真实数据；是否当缺失由数据字典或任务口径决定。
- 纠正：任何聚合（均值、中位数、分组计数、回归）前先显式剔除缺失；**绝不把缺失当 0 参与均值**。
  统计结果里报告 `n_valid` 与 `n_missing`。

## 2. 重复记录（去重前后都要报行数）

- 检测：`profile` 输出 `n_rows`（原始）、`duplicate_rows`（完全重复行数）、`unique_rows`。
- 判断：完全重复行通常可去重；部分键重复（如同一仪器多批次）是否合并由数据字典决定，
  **不能无依据合并**。
- 纠正：分组统计前先决定去重口径，并在 `change_log`/`sources` 记录“去重前 N 行、去重后 M 行”。

## 3. 字段对齐与口径显式化

- 数据字典是字段语义的唯一来源；列名与字典不一致时先做别名映射，不猜。
- 数值字段的单位（如 m vs mm、kg vs g）与有效数字必须与字典一致，换算规则写入 `change_log`。
- 派生字段（如 BMI、比率、差值）写清计算公式与输入字段；不写“看起来合理”的中间值。

## 4. 统计前提检查

- t 检验 / ANOVA：先查正态性与方差齐性；不满足时改用非参数检验（Mann–Whitney U /
  Kruskal–Wallis）或报告前提不满足，而不是照报 p 值。
- 相关系数：区分 Pearson（线性、需近似正态）与 Spearman（秩相关）；报告样本量 n。
- 置信区间 / 标准误：写明估计量与自由度；有效数字统一（一般 3–4 位，p 值例外）。

## 5. SQLite / SQL 查询条件完整性

- 从 `profile` 的 `sqlite` 表结构逐表确认表名、列名、主键。
- WHERE 条件必须覆盖任务要求的全部过滤字段；漏一个字段会多行数、错口径。
- 关联查询（JOIN）写明连接键；聚合查询先确认分组键，避免未去重分组。
- 查询结果行数用 `SELECT COUNT(*)` 独立复核，与 profile 的表行数对照。

## 6. 图表的轴、单位与图例

- x/y 轴标签带单位；图例项与数据中的类别/组名逐字一致。
- 颜色/符号映射与图例一致；不要把两列不同单位的数据画在同一轴上不标注。
- 保存可编辑 SVG（供审阅）与真实 PNG（供快速查看）；`validate` 会检查 SVG 可解析、PNG 存在。

## 7. 结果验证（validate 检查项）

`python3 -B scripts/main.py validate --input /input --output /app/output` 输出
`validation_report.json`，覆盖：result.json 是否为 dict、case_id 是否回填、status 是否合法、
output_contract 字段是否齐全、sources 是否带 locator+quote、主产物文件是否存在、SVG 是否可
解析、PDF 是否以 `%PDF-` 开头、结果是否可 JSON 序列化。

## 8. 证据溯源（sources）

每个关键结论、数值、图元至少一条 `sources`，逐字引用材料原文：

```bash
python3 -B scripts/main.py evidence \
  --locator "materials/data_dict.md#字段口径" \
  --quote "缺失值以空串表示" \
  --role "data_dictionary" \
  --output /app/output --work /app/work
```

同一指标两个来源不一致时两个都记录并标 `source_conflict`，不隐藏。
