# 可复用 SQL 模式与检查规则

以下模板仅作 Agent 撰写定向查询时的参考，运行时以 `/input` 实际 schema 为准，
不预写任何正式案例答案。所有查询都是只读，必须显式写连接条件。

## 1. schema 发现

```sql
SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;
PRAGMA table_info("表名");
SELECT COUNT(*) FROM "表名";
```

## 2. 缺失值（NULL 与空白同视为缺失）

```sql
SELECT COUNT(*) FROM "表名"
WHERE "字段" IS NULL OR TRIM(CAST("字段" AS TEXT)) = '';
```

## 3. 全行重复检测（行数膨胀的第一道防线）

```sql
SELECT "c1","c2",COUNT(*) AS n FROM "表名"
GROUP BY "c1","c2" HAVING COUNT(*) > 1 ORDER BY n DESC;
```

## 4. 值分布（编码型字段先看分布再决定是否转数值）

```sql
SELECT CAST("字段" AS TEXT) AS v, COUNT(*) AS n
FROM "表名" GROUP BY CAST("字段" AS TEXT) ORDER BY n DESC, v ASC LIMIT 10;
```

## 5. 连接键基数（先核基数再 JOIN，避免一对多膨胀）

```sql
SELECT COUNT(*), COUNT(DISTINCT "键") FROM "左表";
SELECT COUNT(*), COUNT(DISTINCT "键") FROM "右表";
-- 两侧 DISTINCT 均等于行数 → one-to-one；一侧相等 → one-to-many/many-to-one
```

## 6. 聚合与分组

```sql
SELECT "分组列", COUNT(*) AS n, AVG(CAST("数值文本列" AS REAL)) AS mean_val
FROM "表名"
WHERE "筛选列" IS NOT NULL
GROUP BY "分组列"
ORDER BY n DESC, "分组列" ASC;
```

## 7. 检查规则（逐条）

1. 只允许 `SELECT/WITH/PRAGMA/EXPLAIN`；任何 `INSERT/UPDATE/DELETE/DROP` 一律拒绝。
2. 聚合前先显式说明 NULL 处理与单位/口径；`CAST(... AS REAL)` 只用于已被标为 `coded_as_text` 的列。
3. 多表查询必须写出连接条件；JOIN 后行数与连接键基数不匹配时记录 `source_conflict`。
4. 每执行一条查询就把 SQL、行数、结果摘要追加到 `/app/work/queries.json`（幂等：只新增，不覆盖）。
5. 结果数值必须能由 `/app/output/query_log.csv` 或 `evidence_map.json` 回查。
