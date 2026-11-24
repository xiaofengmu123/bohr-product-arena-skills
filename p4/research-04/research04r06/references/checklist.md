# 自检清单与评分维度映射

提交前逐条勾选；每一条对应评分维度，用于保证四类可检查产物与证据回链齐备。

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
- [ ] plan.json 已冻结，figure_model.json 覆盖全部文档且 doc_id/locator 可反查。
- [ ] status 与四类产物一致；machine=true 候选已核验或标 unverified。
- [ ] limitations/errors/degradations 如实记录缺失、解析失败与降级。
- [ ] change_log 记录骨架落盘与每次增量；重复运行结果一致。
- [ ] 无编造 benchmark 分数、无预置答案。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 年份/主题/代表作/空白口径 + 排除证据（SKILL.md 第 3/5/6 节） |
| 使用增益 | 25 | 脚本确定性抽取年份/术语/候选句/代表作，减少重复检索、手工补年份与反复核对（scripts/analyze.py） |
| 创新增益 | 15 | plan.json（冻结计划）+ figure_model.json（证据图元模型）+ 年份归一 + 术语首末年份 + 空白/冲突候选（contract.md 第 2/3 节） |
| 可靠体验 | 10 | 空输入/坏 JSON/坏编码/年份缺失/歧义/文献爆炸/PDF 不可读的降级策略（SKILL.md 第 8 节） |
| 生态贡献 | 10 | 文档、契约字典、自检清单、可复算 CSV、figure_model.json（本包全部文件） |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；禁止无证据猜年份。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每类事件写 cause 与证据。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 `coverage_absence`，可排除的写 `open_question`。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selfcheck.json` 定位 FAIL 项；结构错误手工修复 result.json，再复跑 selfcheck。
- 若 `scripts/main.py` 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/main.py --force` → `python3 scripts/analyze.py` → 手工修复 → `python3 scripts/selfcheck.py`。
- selfcheck 退出码：0 = 通过/仅 WARN；1 = 存在 FAIL；2 = result.json 缺失/不可解析。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。
