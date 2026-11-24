# SQL 模板与可复用的查询/检查规则

本文件是 `scripts/dbscan.py` 与 `scripts/main.py` 实际使用的 SQL 片段与规则的说明版，供 Agent 复用、供评审复跑。所有模板均只读，并已做标识符转义。

## 1. 只读连接（必须）

```python
import sqlite3
conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
conn.execute("PRAGMA query_only=ON")
```

只允许 `SELECT / WITH / PRAGMA / EXPLAIN` 开头语句；其余语句一律拒绝并记入 `errors`。

## 2. schema 发现

```sql
SELECT name FROM sqlite_master
WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;

SELECT name, type, "notnull", pk FROM pragma_table_info('表名');
```

表名 / 列名始终用 `quote_ident`（双引号 + 内部双引号翻倍）转义，禁止直接字符串拼接用户可控标识符。

## 3. 行数、缺失、重复

```sql
-- 行数
SELECT COUNT(*) FROM "表";

-- 缺失（NULL 或空白）
SELECT COUNT(*) FROM "表"
WHERE "列" IS NULL OR TRIM(CAST("列" AS TEXT)) = '';

-- 全行重复（按全部列分组）
SELECT "c1","c2",..., COUNT(*) AS c FROM "表"
GROUP BY "c1","c2",... HAVING c > 1;
```

## 4. 描述统计与数值转换规则

- 先按列声明类型与全列非空值判定 `inferred_kind` ∈ `numeric | text | empty`。
- 声明类型含 `int/real/float/double/numeric/decimal/number` 才直接跑 SQL 聚合：

```sql
SELECT COUNT("c"), AVG("c"), MIN("c"), MAX("c"), SUM("c") FROM "表";
```

- 声明类型为文本、但值全为数字的列：标记 `coded_as_text=True`，只在 Python 侧用 `float()` 转换后统计，**禁止**直接对文本列执行 SQL AVG。
- Python 重算与 SQL 聚合交叉核对（`matches_python`），不一致记入 `degradations`。

## 5. 值计数（固定 top-N，确定性排序）

```sql
SELECT CAST("列" AS TEXT) AS v, COUNT(*) AS c FROM "表"
GROUP BY 1 ORDER BY c DESC, v ASC LIMIT 20;
```

## 6. 相关分析（Pearson，确定性）

对数值列两两取 Python 转换后的浮点值，用 `statistics` 语义手工实现 Pearson（不依赖随机种子）：

```
r = Σ((x - mx)(y - my)) / sqrt(Σ(x - mx)^2 · Σ(y - my)^2)
```

样本量 < 2 或方差为 0 时返回 `null`，不补造。

## 7. 定向查询约定（Agent 追加，幂等）

把问题写成只读查询，追加到 `/app/work/queries.json`：

```json
[{"id": "q1", "sql": "SELECT ...", "note": "目的/口径/连接键说明"}]
```

重跑 `scripts/main.py` 后结果进入 `query_log` 与 `answers`，只新增/更新对应条目，不覆盖已产出。
