# 交付前核验清单（操作版）

配合 `SKILL.md` §5 使用。每一行都对应一条评分维度；完成一项勾一项，勾选需以实际文件/命令输出为依据，不靠文字自述。

## 1. 一键自检

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py          # 9 个鲁棒性场景，全 PASS 且退出码 0
python3 -B scripts/main.py validate --input /input --output /app/output
```

`validation_report.json` 的 `valid=true` 且无 `severity=error` 项，才能进入提交。

## 2. 静默错误对照表（"跑通了但算错了"的高危点）

| 静默错误 | 检查方法 |
|-|-|
| 缺失值当 0 | 看 `data_profile.json` 各列 `missing`；非 0 时均值必须显式 `dropna`/排除，并把排除数写进结果 |
| 重复记录未去重 | 看 `duplicate_row_values`；非 0 时必须先判断合并口径（是否按主键去重），把去重前后行数写进 change_log |
| t 检验前提未查 | 小样本先查正态性（Shapiro-Wilk）与方差齐性（Levene）；不满足改用 Mann-Whitney U / Welch，不得直接报 t 的 p 值 |
| SQL WHERE 漏字段 | 把 WHERE 条件逐字对齐任务要求，与 data_profile 的表结构/字段名核对后再执行；记录返回行数 |
| 图轴/单位/图例错 | 图轴标签、单位、图例必须与数据字典单位一致；用 `alignment.json` 核对字段口径 |
| 随机种子/版本未记录 | 固定 `random.seed(42)` 或 `np.random.default_rng(42)`；记录 python 版本、依赖版本、命令行、退出码、日志路径 |
| 字段名对不上 | 用 `align` 生成 `alignment.md`，确认字典字段 ↔ 表头映射；`not_found` 项不得静默忽略 |

## 3. 证据溯源规范

- 每个关键数值/结论/图元在 `sources` 中给一条 `{locator, quote}`，`locator` 用 `材料路径::定位`（如 `materials/data.csv::行3`、`task.md::字段字典`），`quote` 为逐字片段。
- 同一指标两个来源不一致：两条都记，并标 `source_conflict`，不得掩盖。
- 缺值/无法判断写 `not_reported` 或 `pending_human`，不编造引用、DOI、数值。

## 4. 复现清单（生态复用评分）

- [ ] `scripts/main.py snapshot` 已生成 `work/snapshot_manifest.json`（含 sha256），原始材料可复核。
- [ ] 分析代码在 `/app/work/*.py`，运行时依赖只来自 environment 预装清单，依赖版本已写入 result.json 的 `runtime` 或 limitations。
- [ ] 命令行、stdout/stderr、退出码有日志留存；`change_log` 记录每步动作、行数变化、失败与恢复。
- [ ] 主产物可编辑（`.md`/`.json`/`.csv`/`.svg`/Office），且有真实渲染预览（PNG/PDF）；路径在 result.json 与 `artifacts` 中一致且文件真实存在。
- [ ] 他人按 `SKILL.md` §4 最小示例，能从"给数据"独立走到"出结果"，无隐藏手工步骤。

## 5. 完整性检查

- [ ] `case_id` 原样回填；output_contract 全部字段在 result.json 顶层出现，未知键不删、不改名。
- [ ] `status` 诚实：材料不足写 `insufficient_evidence`；中途写 `partial` 并标注 limit。
- [ ] `sources`/`uncertainty`/`pending_human`/`change_log` 齐全；`errors`/`degradations`/`limitations` 无静默遗漏。
