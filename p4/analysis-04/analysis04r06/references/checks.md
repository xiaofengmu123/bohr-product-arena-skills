# 自检清单与降级矩阵（交付前核对）

## 1. 静默错误清单（"跑通了但算错了"重点排查）
| 类别 | 静默错误 | 排查动作 | 对应 IR/脚本 |
|---|---|---|---|
| 缺失值 | 缺失被当 0 参与均值/求和 | 检查 `n_missing`，均值计算显式排除缺失并记录排除数 | profile `missing_excluded`、facts.derivation |
| 重复 | 未去重就分组统计 | 记录去重前/后行数，重复策略写入 rules | profile `duplicate_rows`、plan.rules |
| 统计前提 | t 检验未查正态/方差齐性 | 报 p 值前先记前提检查结论，不满足则改非参数 | plan.rules、facts.check |
| SQL | WHERE 漏字段导致行数偏多 | 查询前后行数对照，WHERE 条件写入 derivation | facts.derivation.inputs |
| 单位 | 图轴/图例单位与字典不一致 | 图元单位必须等于字典单位 | figure_model.axes/evidence |
| 有效数字 | 输出位数与源数据不符 | 统一有效数字，标注口径 | facts.value + unit |
| 随机性 | 种子未留、重跑数字不同 | 固定 `random.seed(42)`/`np.random.default_rng(42)`，记入 change_log | plan.rules |
| 路径 | 路径写死本机、包版本不匹配 | 用 `/input`、`/app/output`、`/app/work`；记录依赖版本 | SKILL.md §6 |

## 2. 评分维度 ↔ 可勾选自检（逐条勾选）
- [ ] 任务完成度（15）：清洗、统计、图表、执行/查询各环节均产出并与任务一致。
- [ ] 结果正确性（15）：无"缺失当 0"、无未去重分组、t 前提已查、SQL WHERE 完整、单位与有效数字一致。
- [ ] 结果完整性（5）：output_contract 全部字段回填，主产物 + 预览齐备。
- [ ] 可核验性（5）：原始快照、规则表、代码、依赖版本、随机种子、命令行、日志、退出码齐全；sources 每项 locator+quote。
- [ ] 质量增益（15）：对照白板/金标准，正确率与一次跑通率提升，且有日志证据。
- [ ] 效率增益（5）：调试轮次、依赖排查、人工改数、复核成本下降，可量化。
- [ ] 增益可验证（5）：公平对照证明，不靠文字自述。
- [ ] 新增能力/方法改进（15）：字段对齐、口径显式化、IR 可 diff、确定性重跑优于裸跑。
- [ ] 调用与易用性（3）：按 SKILL.md §5 独立完成"给数据、出结果"。
- [ ] 运行稳定性（4）：空输入、坏 JSON、坏编码、缺参数四场景退出码 0 且产出合法结果。
- [ ] 异常处理（3）：缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足有识别/重试/降级/转人工。
- [ ] 生态复用（10）：脚本、IR 契约、失败处理说明齐全，他人可直接复用复跑。

## 3. 异常 ↔ 处置策略
| 异常 | 识别 | 重试/降级 | 转人工 |
|---|---|---|---|
| 缺列/类型不符 | profile 字段与字典对比 | 字段别名匹配；失败降级 `.csv` 文本产物 | `pending_human` 列缺口 |
| 依赖缺失 | import 失败、退出码非 0 | 纯标准库替代；无 matplotlib 时只出 SVG | `degradations` 记录 |
| 查询为空 | 结果集 0 行 | 如实写空集/`not_reported` | 不静默跳过 |
| 数值溢出 | OverflowError/NaN | 换口径（对数/缩放），记入 rules | 标注 `not_reported` |
| 权限不足 | PermissionError | 记 `input_error`，不越权重试 | `pending_human` |
| 坏 JSON/坏编码 | 解码/解析异常 | 跳过该文件继续，记 errors | `limitations` 列缺口 |
| 写盘失败 | OSError | 降级最小 result.json（case_id+status+errors） | 退出码仍 0 |

## 4. 交付前一键自检
```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py
```
预期输出全部 PASS，退出码 0。selftest 覆盖：frontmatter、缺参数/--help、空输入、坏 JSON、正常输入 init→profile→seed-plan→render-figure→validate 全链路、SVG 可解析、确定性哈希。

## 5. 复核者检查单（他人复跑）
1. 同一 `/input` 重跑 `init→profile→seed-plan→render-figure→validate`，结果数字一致（确定性）。
2. facts.json 每个 fact 都有 derivation + evidence，quote 能在材料中逐字找到。
3. figure_render.json 的 `data_sha256` 与当前数据一致，SVG 可解析。
4. result.json 的 sources 与 facts 的 evidence 一一对应，无空 locator/quote。
5. 主产物（csv/md/json/SVG/PNG）都存在且路径写入 result.json。
