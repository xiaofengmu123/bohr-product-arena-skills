# 字段语义接地与 SQL 模式（可复用规则）

本文件沉淀三套可复用的规则：字段语义接地、SQL 查询模板、契约解析口径。他人可按此规则在任意授权快照上复跑。

## 1. 字段语义接地规则

数据字典来自 `/input` 中的 `.md/.yaml/.yml/.txt/.json/.csv/.tsv`（`task.md`、`case.json` 除外；`.csv/.tsv` 当表头出现 ≥2 个字段描述关键词时判定为字典，否则判定为数据）。

- **管道表解析**：识别 Markdown 管道表，跳过 `---` 分隔行，定位表头（`field/column/name/variable/字段/列名/变量/属性`），该列作为字段键，其余列作为属性（description/unit/type/range/note…）。
- **键值解析**：非表格行按 `key : value` / `key：value` / `key - value` 提取。
- **CSV 字典解析**：首行为属性头，首列为字段键，其余列作为属性。
- **匹配**：先按归一化名（小写、去非字母数字）精确匹配列名；无精确匹配时按「字典键是列名的子串」做宽松匹配，并写入 `grounded=false` 的未接地列，供 Agent 复核。
- **编码型字段**：声明类型为文本但全部非空值可转数值的列标记 `coded_as_text=true`，聚合时显式 `CAST(col AS REAL)`，不直接对文本列做 `AVG`。
- **易混字段**：当字典中不同条目描述同一物理量（如「样本状态」与「实验状态」）时，按任务书口径选择，并把取舍写入 `uncertainty`。

## 2. 只读 SQL 模板（直接复用）

```sql
-- 行数 / 非空 / 缺失
SELECT COUNT(*) FROM "t";
SELECT COUNT("c") FROM "t" WHERE "c" IS NOT NULL AND "c" <> '';
SELECT COUNT(*) FROM "t" WHERE "c" IS NULL OR "c" = '';

-- 数值描述统计（SQL 侧，用于与 Python 重算对照）
SELECT COUNT(*), AVG("c"), MIN("c"), MAX("c") FROM "t";
-- 编码型文本列先显式转换
SELECT COUNT(*), AVG(CAST("c" AS REAL)), MIN(CAST("c" AS REAL)),
       MAX(CAST("c" AS REAL)) FROM "t";

-- 全行重复检测
SELECT "a", "b", "c", COUNT(*) AS n FROM "t"
GROUP BY "a", "b", "c" HAVING COUNT(*) > 1;

-- 分类计数（确定性排序，在 Python 侧做 top-k）
SELECT "species", COUNT(*) AS n FROM "iris" GROUP BY "species";

-- 跨表连接：显式 ON，杜绝笛卡尔积膨胀
SELECT s."id", COUNT(*) AS n
FROM "samples" s JOIN "measurements" m ON m."sample_id" = s."id"
GROUP BY s."id";
```

**路由与行数核对**：`main.py` 解析 `FROM/JOIN` 引用的表，把查询路由到包含全部引用表的唯一数据源；一个查询命中多个数据源时记为 error（路由歧义），不静默选库。聚合结果行数应等于分组键基数，若大于连接键基数则标 `source_conflict`。

## 3. 输出契约解析口径

`result.json` 的 `output_contract` 字段由 `resolve_contract` 透明建议：

| 契约键特征 | 建议值 | confidence |
|-|-|-|
| 含「count/row/sample/record/样本/记录」且不指向列 | 各表行数之和 | high |
| 含列名 + mean/avg/min/max/std/median/q1/q3/sum | 该列对应统计量 | high（唯一列） |
| 含两个列名 + corr/correlation/相关 | 两列 Pearson r | high |
| 含 duplicate/重复 | 全行重复组数之和 | high |
| 含列名 + 统计但命中多列 | 不写值，记 `pending_human` | medium |

- 仅 `confidence=high` 时才把值写进 contract 字段；其余保持 `null` 并进入 `pending_human`，绝不编造数值。
- 每次写入都记录在 `change_log`（含 ts/step/change/status）。

## 4. 图表选择规则

- 数值列 → 直方图（SVG），标注 mean/median/n/min/max/sd。
- 分类列（≤12 类，top-k）→ 条形图（SVG）。
- 数值列 ≥2 → 相关热力图（SVG，标 r 值，NaN 显示 NA）。
- 所有图表都伴随底层数据（`summary_stats.csv`/`value_counts.json`/`correlation_matrix.csv`），不交「只有图没有数」。
