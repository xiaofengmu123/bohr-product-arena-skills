# 自检清单与评分维度映射（checklist.md）

提交前逐条勾选；每条对应评分维度，用于保证四类可检查产物与证据回链齐备。

## 1. 自检清单（勾选）

- [ ] `result.json` 合法 JSON，含 `case_id`、`output_contract` 全部字段与固定字段（任务完成度/完整性）。
- [ ] 四类产物齐备且可打开：`time_trend.distribution` 有年度计数、`topic_evolution` 有阶段与依据、`representative_works` 有各阶段作品与口径、`research_gaps` 有排除证据（实际效果/完整性）。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本）（结果正确性）。
- [ ] 趋势判断由年度分布支撑，离散波动与拐点已区分（结果正确性）。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注（结果正确性）。
- [ ] 代表作覆盖各阶段且 `criteria` 可说明，不只按被引排序，含长尾方法首创（结果正确性）。
- [ ] 空白项均写 `exclusion_evidence`，区分 `coverage_absence` 与 `open_question`（结果正确性）。
- [ ] 每个 claim 经 `evidence_ids` 回链 `evidence_map`；`quote` 为原文逐字且可定位（结果可核验性）。
- [ ] 冲突证据均记录于 `conflicts`，未静默择一（结果完整性）。
- [ ] `status` 与四类产物一致；`_meta.machine=true` 候选已核验或标 `not_reported`（可靠体验）。
- [ ] `limitations`/`errors`/`degradations` 如实记录缺失、解析失败与降级（异常处理）。
- [ ] `change_log` 记录骨架落盘与每次增量；重复运行分析内容一致（可核验性/稳定性）。
- [ ] 无编造 benchmark 分数、无预置答案、无伪造运行结果（可靠体验）。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 年份/主题/代表作口径 + 空白排除证据（SKILL.md 第 3/6 节，contract.md 第 2–4 节） |
| 使用增益 | 25 | `discover`/`baseline` 机械抽取年份、术语、空白/冲突候选、年度分布，减少重复检索与手工补年份；`report` 一键渲染可编辑产物 |
| 创新增益 | 15 | 预算账本阶段闸门、原子写盘早落骨架、`_baseline` 与核验结论分离、年份来源优先级、代表作按 `kind` 选取（contract.md 第 7 节） |
| 可靠体验 | 10 | 空输入/坏 JSON/年份缺失/歧义/文献爆炸/冲突/PDF 不可读的降级策略（SKILL.md 第 8 节） |
| 生态贡献 | 10 | 契约字典、自检清单、可复算 CSV 表、budget.json 账本、纯标准库可复用脚本 |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（`_baseline.docs[].year_source`：metadata > filename > text_head），再统计；`text_head` 命中必须人工核验，禁止无证据猜年份。
2. **topic_evolution**：对 `_baseline.term_candidates` 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每个事件写 `cause` 与 `evidence_ids`。
3. **representative_works**：按阶段分桶，每阶段按 `kind`（origin/method/milestone/benchmark）覆盖，避免只取高被引；长尾首创（`origin`）优先于总被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 `coverage_absence`，可排除的写 `open_question` 等。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selftest.json` 定位 FAIL 项；结构错误手工修复 `result.json`，再复跑自检。
- 若 `discover` 已落盘骨架，即使后续失败，`result.json` 仍是合法 partial 结果；把失败原因写入 `errors`/`limitations`。
- 恢复顺序：`python3 scripts/main.py discover --force` → `python3 scripts/main.py baseline` → 深度分析（`commit`）→ `python3 scripts/selftest.py` → `python3 scripts/main.py finalize`。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 `machine=true` 候选。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。
