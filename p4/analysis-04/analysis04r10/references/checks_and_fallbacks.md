# 静默错误清单与降级策略

## 一、静默错误核对清单（交付前逐条勾选）

- [ ] **缺失≠0**：所有均值/总和/方差计算前确认缺失值被排除或显式填充；`coerce` 把空串转 `None`，绝不转 `0`。
- [ ] **去重先于分组**：`drop_duplicates` 出现在任何 `groupby`/`describe` 之前；重复键的保留策略显式记录。
- [ ] **t 检验前提**：Welch t 前报告每组 n、均值、标准差、方差齐性提示；正态性未正式检验时显式写明假设未验证，不把 p 值当定论。
- [ ] **SQL WHERE 完整**：查询条件覆盖全部约束字段；用 `row_count` 与预期行数对照；JOIN 时检查是否引入重复行。
- [ ] **轴/单位/图例一致**：图标题、x/y 轴标签、单位与数据字典一致；图例与序列一一对应；SVG 与 PNG 同源。
- [ ] **有效数字/单位统一**：报告值保留与原始精度一致的有效数字；跨批次数据先统一单位再比较。
- [ ] **字段对齐**：`field_alignment.json` 中每个任务字段→数据列→单位→变换均有记录；别名匹配失败时转 `pending_human`，不硬猜。
- [ ] **行数守恒**：清洗前后行数、去重前后行数都写入 `cleaning_trace`；`original_rows ≥ cleaned_rows` 恒成立。
- [ ] **确定性重跑**：种子固定、字典/文件按稳定顺序处理、环境版本与命令行留痕；重跑得到相同结果。

## 二、降级策略（如实标注，绝不编造）

| 场景 | 处置 | 结果字段 |
|---|---|---|
| 输入目录为空/缺失 | 退出码 0，写合法 `result.json`，`status=insufficient_evidence` | `limitations` 列出缺口 |
| 坏 JSON / 坏编码 | 捕获后记录到 `errors`/`degradations`，继续处理其余材料；编码按 utf-8-sig→utf-8→gbk→latin-1 容错 | `errors`、`degradations` |
| 缺列 / 类型不符 | 记 `errors`，尝试大小写不敏感别名匹配；仍失败转 `pending_human` | `pending_human` |
| 依赖缺失（numpy/pandas/matplotlib/scipy 不可用） | 引擎全程标准库；统计用 `statistics`，SQL 用 `sqlite3`，图用内置 SVG+PNG；Agent 的高级分析可额外用 scientific-python，但引擎是保底路径 | `degradations`、`limitations` |
| 查询为空 / 数值溢出 | 如实记为 `not_reported` 或空集，附 SQL 原文与数据库路径，不静默跳过 | `uncertainty`、`errors` |
| 权限不足 / 写盘失败 | 降级写最小合法 `result.json`（case_id+status+errors），退出码仍 0 | `errors` |
| 材料不足 / 金标准缺失 | `status=insufficient_evidence`，`sources` 列出已读证据，`uncertainty` 列出缺口，标 `pending_human` | `pending_human` |

## 三、证据溯源要求

每个关键结论/数值/图元都要有 `{locator, quote, role}`：`locator` 为材料内定位（如 `materials/xxx.csv#row5` 或 `case.json#output_contract`），`quote` 为逐字片段，`role` 说明该证据支撑哪个结论。冲突信息不隐藏：同一指标两个来源不一致时两个都记并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `not_reported` 或 `pending_human`。

## 四、运行稳定性自检

提交前在无输入目录、坏 JSON、缺参数三种情况下各跑一次，全部退出码 0 且产出合法结果；`scripts/selftest.py` 会自动化验证前端 frontmatter、脚本 `--help`、引擎 SVG/PNG 写出与空输入端到端流程。
