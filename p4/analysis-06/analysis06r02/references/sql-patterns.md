# 可复用 SQL 模板与脏数据处理规则

本文件沉淀脚本内部使用的查询模式与规则，供他人直接复用复跑；脚本不依赖本文件，二者口径一致。

## 1. 只读连接

```sql
-- 连接层：URI mode=ro + query_only，双重只读
PRAGMA query_only = ON;
```

```python
import sqlite3
uri = "file:" + str(Path(db).resolve()).replace("\\", "/") + "?mode=ro"
conn = sqlite3.connect(uri, uri=True)
conn.execute("PRAGMA query_only = ON")
```

## 2. schema 与行数

```sql
SELECT name FROM sqlite_master
WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;

PRAGMA table_info("table_name");   -- cid,name,type,notnull,dflt_value,pk
PRAGMA foreign_key_list("table_name");
SELECT COUNT(*) FROM "table_name";
```

## 3. 缺失值（NULL 与空白统一视为缺失）

```sql
SELECT COUNT(*) FROM "t"
WHERE "col" IS NULL OR TRIM(CAST("col" AS TEXT)) = '';
```

## 4. 全行重复

```sql
SELECT "c1", "c2", "c3", COUNT(*) AS n FROM "t"
GROUP BY "c1", "c2", "c3" HAVING COUNT(*) > 1;
```

## 5. 数值统计与 SQL/Python 交叉核对

```sql
SELECT COUNT("x"), AVG("x"), MIN("x"), MAX("x") FROM "t"
WHERE "x" IS NOT NULL AND TRIM(CAST("x" AS TEXT)) <> '';
```

Python 侧用同一列的非空值重算 `mean/min/max`，比较误差 ≤1e-6 记 `matches_python=true`。

## 6. 编码型字段（文本数字）——禁止直接 SQL AVG

- 判定：声明类型为 TEXT，且所有非空值匹配 `^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$`。
- 转换规则：`float(str(v).strip())`，只在 Python 侧聚合；SQL 侧仅做 `COUNT/MIN/MAX(CAST(... AS REAL))` 作为对照，不使用 `AVG(text)`。
- 结果中显式标注 `coded_as_text` 与转换规则。

## 7. 跨表连接键推断与行数膨胀守卫

- 候选键：两表列的去重值集合交集占比 `>= 0.8`。
- 守卫：LEFT JOIN 后行数与左表行数比较，比值 >1 表示右侧键非唯一（一对多），标 `row_expansion`，必须显式写连接条件并核对聚合粒度。
- 主键/标识列（`id`、`*_id`、`*_key`）不参与相关与图表。

## 8. 分组聚合粒度核对

```sql
SELECT "grp", COUNT(*) AS n, AVG("x") AS avg_x FROM "t"
GROUP BY "grp" ORDER BY "grp";
```

核对：`SUM(n)` 应等于表内该列非空行数；`GROUP BY` 后的行数应对应到分组键基数。

## 9. 图表选择（可编辑 SVG，底层数据随附）

- 数值列：直方图（`svg_histogram`）。
- 类别列：频数条形图（`svg_bar`），随附 `value_counts.json`。
- 数值×数值：Pearson 相关热力图 + 最强相关对的散点图（`svg_heatmap`/`svg_scatter`）。
- 每张图必须能对应到底层数据文件（`summary_stats.csv`/`correlation_matrix.csv`/`value_counts.json`），不得只交图不给数。
