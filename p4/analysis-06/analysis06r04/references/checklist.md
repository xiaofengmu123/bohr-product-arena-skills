# 质量红线、SQL 模板与复核清单

本文件是 `SKILL.md` 第 5、6 节的展开版，供 Agent 在执行末段逐项核验、供评审复跑。所有规则可由 `scripts/selftest.py` 自动验证。

## 1. 四类输入场景的确定性要求

| 场景 | 期望行为 | 退出码 |
|-|-|-|
| 正常输入（DB/CSV + case.json + task.md） | 产出完整 report/summary/correlation/figures/evidence，status=partial 或 ok | 0 |
| 空输入目录 | status=insufficient_evidence，pending_human 列缺口，不抛异常 | 0 |
| 坏 JSON / 坏编码 | 记入 errors/degradations，继续处理其余材料 | 0 |
| 缺参数 | `--input/--output/--work` 走默认 `/input`、`/app/output`、`/app/work` | 0 |

验证命令（在 skill 根目录）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py
```

## 2. 逐条红线

1. **证据逐字**：`sources` 与 `evidence_map` 中每个结论都必须带 locator（SQL / `文件#表.字段`）与逐字 quote，不允许只写结论。
2. **SQL 可复跑**：`query_log` 中每条 SQL 记录行数；连接始终只读（`mode=ro` + `PRAGMA query_only=ON`）；只允许 `SELECT/WITH/EXPLAIN` 与只读 PRAGMA。
3. **行数膨胀**：跨表查询必须显式写连接条件；GROUP BY 后行数必须能对应到连接键基数，否则标 `source_conflict`。同名共享列只标「候选连接键」，不擅自断言外键。
4. **编码型字段**：声明类型非数值、但值全为数字的列，标记 `coded_as_text` 并在 Python 侧转换；`conversion_note` 必须写清「非数值当缺失」的比例，禁止直接对文本列用 SQL AVG。
5. **缺失/重复**：`NULL` 与空白视为缺失，逐列计数；全行重复用 `GROUP BY 全列 HAVING COUNT(*)>1` 检测并记录组数与涉行数。
6. **口径一致**：数值统计同时给样本量 `n` 与均值 95% CI（`stdev/√n` 近似正态），SQL 聚合与 Python 重算做交叉核对；不一致必须显式说明。
7. **不编造**：没有数据、没有软件、没有执行记录的步骤，一律写 `null`/`pending_human`，不得填占位数值或伪造「已通过」。

## 3. status 判定参考（与 `main.py:determine_status` 一致）

- `ok`：数据可查、contract 字段全部非 null、无 errors、无 degradations。
- `partial`：有可交付结果但存在未覆盖维度、未填 contract 字段、可解释的降级或待人工项。
- `insufficient_evidence`：无 DB/CSV，或任务要求的证据完全缺失。
- `input_error`：case.json 等输入本身不可解析且无法继续（保留骨架）。

## 4. 降级与不确定性登记表

| 触发条件 | 记录位置 | 处理 |
|-|-|-|
| 无 DB/CSV | `pending_human` + `limitations` | status=insufficient_evidence |
| 连接失败/权限不足 | `errors` + `degradations` | 跳过该库，继续其余材料 |
| 文本数字编码字段 | `uncertainty`(low) + `conversion_note` | Python 侧显式转换 |
| 部分值非数值 | `uncertainty`(medium) | 非数值当缺失并写明比例 |
| 均值估计精度 | `uncertainty`(low) | 附 95% CI 与假设说明 |
| 共享列候选连接键 | `uncertainty`(medium) | 标「候选」，不擅自聚合 |
| 样本不足无法计算 | 结果字段写 `null` | 注明原因，不补造 |

## 5. 常用只读 SQL 模板（可复用）

```sql
-- schema 检索
PRAGMA table_info("table_name");
SELECT name, type FROM sqlite_master
  WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name;

-- 行数与缺失（NULL 与空白都算缺失）
SELECT COUNT(*) FROM "t";
SELECT COUNT(*) FROM "t" WHERE "col" IS NULL OR trim("col") = '';

-- 全行重复
SELECT col1, col2, COUNT(*) AS n FROM "t"
  GROUP BY col1, col2 HAVING COUNT(*) > 1 ORDER BY n DESC;

-- 描述统计（数值列交叉核对用；SQL 与 Python 双侧都要有）
SELECT AVG("col"), MIN("col"), MAX("col"), COUNT("col") FROM "t";

-- 跨表连接：显式连接条件 + 去重控制一对多膨胀
SELECT a."id", COUNT(DISTINCT b."id") AS n_b
  FROM "a" LEFT JOIN "b" ON b."a_id" = a."id"
  GROUP BY a."id";
```

## 6. 交付前最终核对（提交 Skill 本身）

- [ ] `SKILL.md` YAML frontmatter 含 `name`/`description`/`version`，正文 ≤180 行。
- [ ] `scripts/*.py` 仅标准库，`python3 -B` 可运行，路径相对 `scripts/...`。
- [ ] `scripts/selftest.py` 全绿（空目录/坏 JSON/正常 DB/CSV/查询回填/确定性）。
- [ ] 不包含 `__pycache__`、`*.pyc`、示例答案、预写正式测试产物。
- [ ] 总文件数 ≤14，总体积 ≤1.2MB；单文本文件 ≤500KB。
