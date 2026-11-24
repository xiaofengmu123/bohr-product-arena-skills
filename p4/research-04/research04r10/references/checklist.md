# 自检清单与评分维度映射

提交/交付前逐条勾选；每一条对应评分维度，用于保证四类可检查产物与证据回链齐备。

## 1. 自检清单（勾选）

- [ ] result.json 合法 JSON，含 case_id、output_contract 全部字段与固定字段。
- [ ] 四类产物齐备：time_trend.distribution / topic_evolution / representative_works / research_gaps。
- [ ] 年份口径写于 protocol.date_definition_note 与 time_trend.unit_note（在线发表/见刊/预印本）。
- [ ] 趋势判断有年度分布支撑，离散波动与拐点已区分（inflection_notes）。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注于 term_mapping。
- [ ] 代表作覆盖各阶段且 criteria 可说明，不只按被引排序，含长尾方法首创（kind=origin/method）。
- [ ] 空白项均写 exclusion_evidence，区分 coverage_absence 与 open_question。
- [ ] 每个 claim 经 evidence_ids 回链 evidence_map；quote 为原文逐字。
- [ ] 冲突证据均记录于 conflicts，未静默择一。
- [ ] status 与四类产物一致；machine=true 候选已核验或写入 limitations 标 unverified。
- [ ] limitations/errors/degradations 如实记录缺失、解析失败与降级。
- [ ] change_log 记录 init/extract/compile 各步；重复运行结果一致。
- [ ] 无编造 benchmark 分数、无预置答案。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 口径说明 + 空白排除证据（SKILL.md 第 1/3/5 节） |
| 使用增益 | 25 | 脚本机械抽取年份/术语/候选，减少重复检索、手工补年份与反复核对（scripts/core_impl.py 的 extract） |
| 创新增益 | 15 | 证据台账 + 编译器架构、先验台账后编译的校验顺序、年份归一、术语首末行、空白/冲突候选（references/method.md） |
| 可靠体验 | 10 | 空输入/坏 JSON/坏编码/年份缺失/歧义/文献爆炸/PDF 不可读的降级策略（SKILL.md 第 7 节） |
| 生态贡献 | 10 | SKILL.md、契约字典、自检清单、可复算 CSV、claims.json 模板（本包全部文件） |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（`features.json.doc_years[*].source`：metadata 优先，其次 text_header，缺失不猜），再统计；`distribution` 与 `sources` 数量一致，缺失年份不计数。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每类事件写 cause 与 evidence_ids。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 `coverage_absence`，可排除的写 `open_question`。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selftest.json` 定位 FAIL 项（S01–S12）；结构错误手工修复 `claims.json` 或材料，再 `python3 scripts/main.py compile`，复跑 selftest。
- 若 `init` 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/main.py init --force` → `python3 scripts/main.py extract` → 写 claims.json → `python3 scripts/main.py compile` → `python3 scripts/selftest.py`。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，selftest 硬检查全过，无未核验的 machine 候选进入结论。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。

`status` 由 `compile` 按产物完整性与证据回链自动计算，不由脚本硬编码 `ok`；模型通过 claims.json 的 `status_hint` 只能显式降级（如 `insufficient_evidence`），不能凭空升级。
