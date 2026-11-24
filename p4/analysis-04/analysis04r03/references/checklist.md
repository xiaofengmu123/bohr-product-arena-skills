# 自检与验收清单（scripts/selftest.py 对应项）

本清单是 SKILL.md §5 的逐条展开，供 Agent 提交前核对，也供评审复核。分号后为对应评分维度。

## A. 任务完成度（15 分）
- [ ] 已从 case.json/task.md 读到数据字典、字段语义、约束与 output_contract。
- [ ] 清洗环节产出清洗表（保留原始快照，去重/缺失策略可解释）。
- [ ] 统计环节产出统计结果（均值/方差/分位数/分组/假设检验）。
- [ ] 图表环节产出可编辑 SVG + 真实 PNG 预览，轴/单位/图例与原始数据一致。
- [ ] 执行/查询环节跑通，SQLite 查询保留完整 SQL 与行数。

## B. 结果正确性（15 分）
- [ ] 缺失值未当 0 参与均值；缺失计数与 present 计数分开记录。
- [ ] 重复记录未在分组统计前被静默合并；记录去重前后行数。
- [ ] 两样本检验用的是 Welch t 检验；正态性（`normality_screen`）与方差齐性（`levene_median`）均已报告。
- [ ] SQL WHERE/JOIN 条件完整，行数与预期一致；查询为空如实记录空集。
- [ ] 单位与有效数字一致；浮点先 `round` 再序列化。

## C. 结果完整性（5 分）
- [ ] result.json 含 case_id + output_contract 全部字段（缺值 null/[]）。
- [ ] 主产物路径回填契约字段；可编辑产物 + 预览齐备。
- [ ] status 诚实（ok/partial/insufficient_evidence/input_error）。

## D. 结果可核验性（5 分）
- [ ] 每个关键结论/数值/图元有 sources 项：locator + 逐字 quote + role。
- [ ] 保留：输入指纹、随机种子 42、Python 版本、命令行、退出码、日志。
- [ ] 生成自包含 `analysis_code.py` 供他人独立复算。

## E. 可靠性 / 稳定性 / 异常（3+4+3 分）
- [ ] 空输入、坏 JSON、坏编码、缺参数四种场景退出码 0 且产出合法结果（selftest 覆盖）。
- [ ] 缺列、类型不符、依赖缺失、查询为空、数值溢出、权限不足均有识别/重试/降级/转人工策略。
- [ ] 写盘失败降级到最小合法 result.json，进程不崩溃。

## F. 确定性（本版重点）
- [ ] 同输入两次运行 `result.json` 逐字节一致（selftest 断言）。
- [ ] 墙钟时间只在 `runtime.json`，不进 result.json。
- [ ] 所有循环按排序键；`json.dumps(sort_keys=True)`。

## 运行方法
```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py   # 期望输出 31 passed, 0 failed，退出码 0
```
