# 自检清单与评分维度映射

提交前逐条勾选；每条对应一个评分维度，用于保证四类可检查产物、证据回链与鲁棒性齐备。脚本侧校验命令：`python3 scripts/selftest.py --result /app/output/result.json`；鲁棒性证明：`python3 scripts/selftest.py --smoke`。

## 1. 自检清单（逐条勾选）

- [ ] result.json 合法 JSON，含 case_id、output_contract 全部字段与固定字段。
- [ ] 四类产物齐备：time_trend.distribution / topic_evolution / representative_works / research_gaps。
- [ ] 年份口径写于 protocol.date_definition_note 与 time_trend.unit_note（在线发表/见刊/预印本）。
- [ ] 趋势判断有年度分布支撑，离散波动与拐点已区分。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注。
- [ ] 代表作覆盖各阶段且 criteria 可说明，不只按被引排序，含长尾方法首创。
- [ ] 空白项均写 exclusion_evidence，区分 coverage_absence 与 open_question。
- [ ] 每个 claim 经 evidence_ids 回链 evidence_map；quote 为原文逐字。
- [ ] 冲突证据均记录于 conflicts，未静默择一。
- [ ] status 与四类产物一致；machine=true 候选已核验或标 unverified。
- [ ] 空输入/坏 JSON/缺参数/超大文件四类异常均退出码 0 且落盘合法 result.json。
- [ ] limitations/errors/degradations 如实记录缺失、解析失败与降级。
- [ ] change_log 记录骨架落盘与每次增量；重复运行结果一致。
- [ ] 无编造 benchmark 分数、无预置答案。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 口径说明 + 空白排除证据（SKILL.md 第 1/3/5 节） |
| 使用增益 | 25 | 脚本机械抽取年份/术语/候选，减少重复检索、手工补年份与反复核对（scripts/main.py + core_impl.py） |
| 创新增益 | 15 | 先落盘后增量的骨架流程、年份归一、术语首末年份、空白/冲突候选、代表作按 kind 分桶（SKILL.md 第 4/8 节） |
| 可靠体验 | 10 | 空输入/坏 JSON/缺参数/超大文件/坏编码/PDF 不可读的降级矩阵（SKILL.md 第 7 节） |
| 生态贡献 | 10 | 文档、契约字段、自检脚本、可复算 CSV 表（本包全部文件） |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；禁止无证据猜年份。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每类事件写 cause 与证据。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 `coverage_absence`，可排除的写 `open_question`。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selfcheck.json` 定位 FAIL 项；结构错误手工修复 result.json，再复跑 `scripts/selftest.py --result`。
- 若 `main.py` 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/main.py`（幂等重跑）→ 手工修复 result.json → `python3 scripts/selftest.py --result /app/output/result.json`。
- 本地自证鲁棒性：`python3 scripts/selftest.py --smoke`（仅合成输入，不包含正式案例与答案）。

## 5. status 判定规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选。
- `partial`：产物可打开但不完整，或部分结论待人工复核（脚本机械基线默认此值）。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复（如 case.json 坏且无回退 id）。
