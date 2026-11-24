# SQL 模式与检查规则（可复用模板）

本文件是只读科研快照查询的可复用 SQL 模板与复核规则，供 Agent 在阶段 2 定向查询时复用，供他人复跑。
所有模板假定表/字段名已用 `PRAGMA table_info` 核对；`"..."` 处必须替换为真实标识符（用双引号包裹）。

## 1. 只读连接（必须）

```sql
-- 连接（脚本已执行）：
--   sqlite3.connect("file:<abs_path>?mode=ro", uri=True)
PRAGMA query_only=ON;
```

安全约束：只允许 `SELECT/WITH/PRAGMA/EXPLAIN`；写语句一律拒绝。

## 2. schema 检索（先发现，再假设）

```sql
SELECT name, type FROM sqlite_master
 WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
 ORDER BY type, name;

PRAGMA table_info("<table>");        -- 字段名/类型/主键/非空
PRAGMA foreign_key_list("<table>");  -- 外键（连接键候选）
PRAGMA index_list("<table>");        -- 索引/唯一约束
SELECT COUNT(*) AS n FROM "<table>";
```

## 3. 缺失 / 重复 / 编码型字段

```sql
-- 缺失：NULL 与空白同视为缺失
SELECT COUNT(*) AS n FROM "<t>" WHERE "<c>" IS NULL OR CAST("<c>" AS TEXT)='';

-- 全行重复（组数与涉行数）
SELECT "<c1>", "<c2>", COUNT(*) AS n FROM "<t>"
 GROUP BY "<c1>", "<c2>" HAVING COUNT(*) > 1
 ORDER BY n DESC;

-- 编码型字段转数值（Python 侧转换规则，禁止直接对文本列 AVG）
-- 规则：text -> float；非数值/空白记缺失；转换结果写入 plan.json.field_semantics
```

## 4. 聚合与分组

```sql
-- 描述统计（数值列，声明类型为数值）
SELECT COUNT(*) AS n, AVG("<c>") AS avg, MIN("<c>") AS mn, MAX("<c>") AS mx,
       SUM("<c>") AS total
  FROM "<t>" WHERE "<c>" IS NOT NULL;

-- 分组计数（类别列）
SELECT CAST("<c>" AS TEXT) AS value, COUNT(*) AS n
  FROM "<t>" WHERE "<c>" IS NOT NULL AND CAST("<c>" AS TEXT) <> ''
 GROUP BY 1 ORDER BY n DESC, value ASC LIMIT 12;
```

复核规则：`AVG` 结果必须与 Python `statistics.fmean` 交叉核对（脚本自动输出 `matches_python`）。

## 5. 跨表连接与行数膨胀控制

```sql
-- 连接前先算两侧行数与连接键基数，再执行 INNER JOIN
SELECT (SELECT COUNT(*) FROM "<left>")  AS n_left,
       (SELECT COUNT(*) FROM "<right>") AS n_right,
       COUNT(*) AS n_joined
  FROM "<left>" l JOIN "<right>" r ON l."<lk>" = r."<rk>";
```

红线：
- 连接键优先取 `foreign_key_list` 的结果；无外键时用同名 + 取值重叠推断候选，但只记为 `join_candidates`，不自动聚合成结论。
- 若 `n_joined` 大于 `n_left + n_right` 中较大一侧且连接键在右表不唯一（一对多），须标注 `source_conflict` 或改用去重后的右表。
- GROUP BY 后行数应能对应连接键基数；不一致时写明原因，不静默。

## 6. 结果抽取与回查

- 每条查询写入 `/app/work/queries.json`：`[{"id":"q1","sql":"...","note":"问题与口径"}]`。
- 重跑 `main.py` 后，结果进入 `query_log`（含 SQL、行数、摘要）与 `answers`，可回查。
- 数值复述前先读 `result.json`/`summary_stats.csv`/`plan.json`，不凭记忆。

## 7. 字段语义解释模板（阶段 1 写回 plan.json）

对每个关键字段记录：含义、单位、取值范围、统计口径、易混字段辨析（如样本状态 vs 实验状态）、编码规则（0/1、文本数字）、依据（数据字典定位）与置信度。数据字典与内置先验冲突时，以数据字典为准。
