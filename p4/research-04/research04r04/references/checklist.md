# 自检清单与评分维度映射

提交前逐条勾选；每条对应评分维度，用于保证四类可检查产物、证据回链与诚实降级齐备。`scripts/selftest.py` 自动执行「结构/枚举/quote 可定位」部分，其余由 Agent 按清单人工核验后勾选。

## 1. 自检清单（逐条勾选）

- [ ] `result.json` 合法 JSON，含 `case_id`、`output_contract` 全部字段（原样回填）与第 1 节固定字段。
- [ ] `status` 与四类产物一致；`ok` 仅在四类齐全且关键 claim 均有证据回链时给出。
- [ ] 四类产物齐备：`time_trend.distribution` / `topic_evolution` / `representative_works` / `research_gaps`。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本）。
- [ ] 趋势判断有年度分布支撑，离散波动与真实拐点已区分，未把关键词热度当趋势。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注于 `term_mapping`。
- [ ] 代表作覆盖各阶段且 `criteria` 可说明，不只按被引排序，含长尾方法首创（`kind=origin/method`）。
- [ ] 空白项均写 `exclusion_evidence` 与 `counter_example_check`，区分 `coverage_absence` 与 `open_question`。
- [ ] 每个关键 claim 经 `evidence_ids` 回链 `evidence_map`；`quote` 为原文逐字；`selftest.py` 无「未定位」硬错误。
- [ ] 每条结论先定边界（`reported/inferred/not_reported`）再给 `confidence`（`high/medium/low/unverified`）。
- [ ] 冲突证据两方均记录于 `conflicts`，未静默择一。
- [ ] 每条 `uncertainty` 有 `kind`/`location`/`impact`/`mitigation`，指向受影响字段。
- [ ] `limitations`/`errors`/`degradations` 如实记录缺失材料、解析失败与降级，无编造。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论。
- [ ] `change_log` 记录骨架落盘与每次增量；`runtime` 不含 benchmark/白板/分数。
- [ ] 重复运行结果一致（`python3 scripts/selftest.py --demo` 四场景 PASS）。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 三层证据边界 + 四级置信度 + 空白排除证据（`SKILL.md` 第 1/3/5 节、`contract.md` 第 1/2 节） |
| 使用增益 | 25 | `main.py prepare` 扫描发现 + `main.py analyze` 机械抽取年份/术语/候选句/代表作候选，减少重复检索与手工补年份；`main.py run` 一键执行 |
| 创新增益 | 15 | 三层证据边界、四级置信度、不确定性账本、降级状态机、`coverage` 显式区分证据排除与检索遗漏 |
| 可靠体验 | 10 | 空输入/坏 JSON/年份缺失/歧义/文献爆炸/冲突的降级阶梯与 `selftest.py --demo` 验证 |
| 生态贡献 | 10 | 契约字典、自检清单、可复算 CSV 表、证据映射 `evidence_map.json` |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；`year_source=heuristic` 的文档不得用于宣称精确拐点。
2. **topic_evolution**：对 `term_mapping` 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每类事件写 `cause` 与证据，同名异义写 `ambiguity_note`。
3. **representative_works**：按阶段分桶，每阶段按 `kind`（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + `counter_example_check`；无法排除的写 `coverage_absence`，可排除的写 `open_question`。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selfcheck.json` 定位 FAIL 项；结构错误手工修复 `result.json`，再复跑 `python3 scripts/main.py selfcheck`。
- 若 `prepare` 已落盘骨架，即使后续失败，`result.json` 仍是合法 partial 结果；把失败原因写入 `errors`/`limitations`。
- 恢复顺序：`python3 scripts/main.py prepare` → `python3 scripts/main.py analyze` → 手工核验四类产物 → `python3 scripts/selftest.py`。
- 不伪造软件状态、数据、文献或分数；partial 结果必须标明边界。

## 5. status 判定

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验 machine 候选影响关键结论。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。
