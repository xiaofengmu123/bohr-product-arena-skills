# 检查清单与降级矩阵

本文件是 `SKILL.md` 第 5/6 节的展开，交付前逐条勾选。所有检查都要给出 `PASS/FAIL` 与定位，FAIL 项必须在 `result.json` 的 `errors`/`degradations`/`uncertainty` 中有对应记录，不能只改状态不写原因。

## A. 骨架与增量

- [ ] 前 3 分钟内已运行 `scripts/main.py scaffold`，`/app/output/result.json` 存在且 `status=partial`。
- [ ] 契约键全部以 `null`/`[]` 占位，不出现缺失键。
- [ ] 后续每次落盘是增量更新（`change_log` 追加一条），不重写已核实的数值结论。

## B. 输出契约

- [ ] `case_id` 非空，来源可指回 `case.json` 或目录名（后者已在 `degradations` 说明）。
- [ ] `result.json` 含题目 `output_contract` 的全部键（原样、不改名、不删未知键）。
- [ ] `status` ∈ {ok, partial, insufficient_evidence, input_error}，且与真实完成情况一致。
- [ ] `sources` 每项有 `locator` + 逐字 `quote`；关键结论（清洗删行数、p 值、r、效应量）都能指回材料或 `stats.json`。
- [ ] `uncertainty`、`limitations`、`pending_human`、`errors`、`degradations`、`change_log`、`runtime` 齐全。

## C. 清洗一致性

- [ ] `clean_meta.json.input_rows - clean_meta.json.output_rows == clean_meta.json.dropped_duplicates`。
- [ ] `cleaned.csv` 数据行数 == `clean_meta.json.output_rows`（`scripts/selftest.py` 自动核对）。
- [ ] 异常值只标记（`outlier_by_column`），`rows_removed=0`；未在 task.md 明确要求时不得删除。
- [ ] 缺失值只计数不填补；若 task.md 要求填补，规则与填补值写入 `clean_meta.json.rules`。

## D. 统计口径

- [ ] 组数=2 → Welch t + Mann-Whitney；组数≥3 → ANOVA + Kruskal-Wallis；Levene 检验方差齐性。
- [ ] 方差不齐（Levene p<0.05）时报告用 Welch/Kruskal-Wallis，并写 `pending_human` 提示人工确认。
- [ ] 每个检验报告统计量、自由度、p 值、效应量；`p=0` 渲染为 `<1e-300` 并在 `uncertainty` 说明浮点下溢。
- [ ] 相关分析同时给 Pearson r 与 Spearman ρ；n 为成对完整样本数。

## E. 可视化与报告

- [ ] SVG 的箱线分位、散点回归线、直方图频数与 `stats.json` 同源，坐标轴有单位/误差含义。
- [ ] `report.md` 正文每个数字都能指回 `stats.json`，无手写数字。
- [ ] `xml.etree` 能解析每个 SVG；`stats.json`/`report.md` 编码为 UTF-8。

## F. 确定性与重跑

- [ ] 同一输入两次运行：`stats.json` 与所有 SVG 字节一致（`scripts/selftest.py` 或 `cmp`/`fc` 验证）。
- [ ] 全脚本幂等，重跑不报“文件已存在”类错误。

## G. 降级矩阵

| 场景 | 处置 | status | 记录位置 |
|---|---|---|---|
| `/input` 目录不可读 | 立即失败但不崩溃 | `input_error` | `errors` |
| 无任何数据文件 | 输出材料不足 | `insufficient_evidence` | `limitations` |
| `case.json` 缺失但有数据 | 用目录名作 case_id | `partial` | `degradations` |
| `case.json` 坏 JSON | 用默认契约 | `partial` | `degradations` |
| `output_contract` 缺失 | 用默认三键 | 不变 | `degradations` |
| 列类型不符/全空 | 跳过该列继续其余 | 不变 | `degradations` |
| 除零/空组/样本不足 | 该检验返回 skipped | 不变 | `uncertainty` |
| 编码异常 | utf-8-sig→utf-8→latin-1→gbk | 不变 | `degradations` |
| 缺 matplotlib/pandas/scipy | 用纯标准库 SVG 与手写统计 | 不变 | `limitations` |
| xlsx/xls 无 openpyxl | 跳过该文件 | 不变 | `limitations` |
| SQLite 连接/表失败 | 跳过该表 | 不变 | `errors` |

## H. 证据溯源规范

- **输入事实**（表头、行数、任务要求）→ `quote` 必须是材料中的逐字片段。
- **推导结果**（p 值、r、效应量）→ `locator` 指到 `work/stats.json`，`quote` 写精确的 `key=value` 形式（如 `"p_value": 0.0`）。
- 两个来源冲突 → 都记录并标 `source_conflict`，不隐瞒、不二选一。
- 缺失 → 写 `pending_human` 或 `not_reported`，不虚构数值/DOI/结论。
