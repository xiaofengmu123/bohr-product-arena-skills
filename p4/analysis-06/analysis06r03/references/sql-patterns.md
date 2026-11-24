# 可复用 SQL 模式与检查规则

以下模板针对只读 SQLite 快照编写，均可直接替换表名/列名后复跑。所有查询只读，不改变数据。

## 1. 表与 schema 发现

```sql
-- 列出全部用户表（按表名固定排序，保证重复运行一致）
SELECT name FROM sqlite_master
WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
ORDER BY name;

-- 单表字段定义
PRAGMA table_info("表名");

-- 精确行数（含 NULL）
SELECT COUNT(*) FROM "表名";
```

## 2. 缺失值（NULL 与空白一律视为缺失）

```sql
SELECT COUNT(*) AS missing
FROM "表名"
WHERE "列名" IS NULL OR TRIM(CAST("列名" AS TEXT)) = '';
```

## 3. 全行重复检测（不遗漏 NULL 列）

```sql
SELECT COUNT(*) AS n,
       COALESCE(CAST("c1" AS TEXT),'<NULL>'),
       COALESCE(CAST("c2" AS TEXT),'<NULL>')
FROM "表名"
GROUP BY COALESCE(CAST("c1" AS TEXT),'<NULL>'),
         COALESCE(CAST("c2" AS TEXT),'<NULL>')
HAVING COUNT(*) > 1
ORDER BY n DESC;
```

重复组数 = 结果行数；涉行数 = 各组 `n` 之和。判断依据：SQL 聚合与 Python 重算一致。

## 4. 编码型字段（声明为 TEXT 但值全为数字）

先探测，不直接聚合：

```sql
SELECT "列名", COUNT(*) FROM "表名"
WHERE "列名" IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
```

若所有非空值都能解析为数字，则在 Python 侧 `float()` 转换后统计，并标记 `coded_as_text=1`。禁止对文本列直接 `AVG("列名")`。

## 5. 分组聚合与独立复算

```sql
-- SQL 侧
SELECT "分组列", COUNT(*) AS n, AVG("数值列") AS avg
FROM "表名"
WHERE "数值列" IS NOT NULL
GROUP BY "分组列"
ORDER BY "分组列";
```

Python 侧用 `statistics`/手动求和重算同一组内 `mean/min/max`，与 SQL 结果交叉核对；不一致写 `uncertainty` 并标 `sql_python_mismatch`。

## 6. 跨表连接与行数膨胀控制

```sql
-- 一对多连接：先确认连接键基数，再连接，最后核对行数
SELECT a."连接键", COUNT(DISTINCT b."主键") AS n_b
FROM "左表" a
LEFT JOIN "右表" b ON a."连接键" = b."连接键"
GROUP BY a."连接键";
```

规则：若 `n_b > 1`，说明右表对同一键有多行，聚合前必须明确 `COUNT(DISTINCT …)` 或先在右表按键聚合成一行，避免行数膨胀。连接后的行数必须能与连接键基数对应，否则标 `source_conflict`。

## 7. 时间 / 筛选条件

```sql
-- 时间区间（列类型以数据字典为准；文本日期需先标准化）
SELECT * FROM "表名"
WHERE "时间列" >= '2024-01-01' AND "时间列" < '2025-01-01';
```

规则：时间范围写成左闭右开，避免边界重复计数；文本日期先转成 ISO 字符串再比较，转换规则写入 `uncertainty` 或 `change_log`。
