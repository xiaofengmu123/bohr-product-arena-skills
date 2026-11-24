# 静默错误清单与核对规则（执行前必读）

本清单是"跑通 ≠ 算对"的具体展开。Step 4 逐条对照，任何不一致先调查、后修正，不掩盖。

## A. 清洗与统计口径

1. **缺失值不得当 0**：均值/中位数/标准差、分组聚合前，先按缺失标记（空串、`NA`、`nan`、`null`、`-`）剔除或显式填补，并在 change_log 记录策略与受影响行数。用 `data_profile.json` 的 `missing` 列核对。
2. **重复记录先判断再处理**：分组统计前确认是否需要去重（同一实体多条记录该不该合并由数据字典决定）。用 `data_profile.json` 的 `duplicate_rows` 核对去重前后行数。
3. **t 检验/参数检验先查前提**：正态性（Shapiro-Wilk）、方差齐性（Levene）、样本量；不满足则改用非参数（Mann–Whitney U / Wilcoxon）或报告前提违背，不得直接报 p 值。
4. **口径显式化**：每个统计量写清"对哪些行、哪些列、什么聚合、什么单位、有效数字几位"。单位换算（如 mm→cm、百分比→比例）显式声明。
5. **有效数字一致**：报告数值与原数据精度匹配；中间计算不四舍五入，仅在最终报告统一。

## B. 数据库查询

6. **SQL WHERE 条件完整**：涉及多条件筛选时，逐字段核对是否漏掉"按组别/按时间窗/按状态"等约束；漏一个字段会直接导致行数偏多。写清每个条件的来源（字段名 + 取值）。
7. **连接与去重**：多表 JOIN 后核对行数是否符合预期（是否产生笛卡尔积、是否需 `DISTINCT`）。
8. **查询为空如实报告**：返回 0 行是合法结果，写 `empty_result`，不得悄悄换成别的查询。

## C. 图表

9. **坐标轴/单位/图例与原始数据一致**：轴标签写字段全称与单位；图例名称 = 分组取值；数据点顺序与清洗表一致。
10. **可编辑 + 可预览**：图形同时交 SVG（可编辑）+ PNG/PDF（真实预览）；SVG 可被 XML 解析，PDF 以 `%PDF-` 开头。

## D. 复现与证据

11. **固定随机种子**：任何抽样、打乱、聚类、缺失填补用 `random.seed(42)` / `np.random.default_rng(42)`，并写入日志。
12. **记录环境与命令**：Python 版本、关键包版本、完整命令行、stdout/stderr、退出码落盘到 `/app/work/`。
13. **逐字溯源**：result.json 的 `sources` 每项 `{locator, quote, role}`，quote 必须是从材料逐字复制的字符串，不是转述。冲突信息两个都记并标 `source_conflict`。

## E. 异常处置速查

| 场景 | 处置 |
|---|---|
| 缺列 / 字段名与字典不符 | 记 errors，尝试大小写/别名匹配；仍失败转 `pending_human` |
| 类型不符（数字列混入文本） | 记 errors，说明剔除/强转规则及受影响行数 |
| 依赖缺失（matplotlib/SQLite 等） | 降级纯标准库或 `.csv`/`.md` 文本，记 `degradations` |
| 查询为空 | 记 `not_reported` 或空集，不静默跳过 |
| 数值溢出 / inf / NaN 结果 | 记 errors，改算法或标 `pending_human` |
| 权限不足 / 写盘失败 | 降级最小合法 result.json（case_id+status+errors），退出码 0 |
| 文件 > 2MB 或二进制 | 跳过并记 `skipped`，不当作表格强解 |

## F. 交付前最后一查（可执行）

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py                                   # 空输入/坏JSON/坏编码/缺参数
python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
```

`validation.json` 的 `passed=true` 是交付的必要条件；若为 false，逐条修复 `checks` 里的失败项再交。
