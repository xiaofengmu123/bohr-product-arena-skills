# 自检清单与评分维度映射

提交/交付前逐条勾选；每一条对应评分维度，用于保证四类可检查产物与证据回链齐备。自检脚本 `scripts/selftest.py` 只覆盖可自动化的部分（契约、枚举、quote 可定位、证据回链、无伪造分数）；以下人工项需由 Agent 在收尾阶段逐条核对。

## 1. 自检清单（勾选）

- [ ] result.json 合法 JSON，含 case_id、output_contract 全部字段与固定字段（含 status/sources/uncertainty/change_log）。
- [ ] 四类产物齐备：time_trend.distribution / topic_evolution / representative_works / research_gaps，且均非空。
- [ ] 年份口径写于 protocol.date_definition_note 与 time_trend.unit_note（在线发表/见刊/预印本），并记录年份来源。
- [ ] 趋势判断有年度分布支撑，离散波动与拐点已区分；turning_points 不把单年噪声当拐点。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已在 term_mapping 标注。
- [ ] 代表作覆盖各阶段且 criteria 可说明，不只按被引排序，含长尾方法首创（kind=method/origin）。
- [ ] 空白项均写 exclusion_evidence（检索式 + 排除条件 + 反例排查），区分 coverage_absence 与 open_question。
- [ ] 每个 claim 经 evidence_ids 回链 evidence_map.items；每条 evidence 的 used_by_claims 反向回指，无悬空引用。
- [ ] quote 为原文逐字；locator 指向 materials/ 内实际行号；quote_hash 已生成且可复算。
- [ ] 冲突证据均记录于 conflicts.items，未静默择一；无法裁决写 resolution=未决。
- [ ] status 与四类产物一致；machine_candidates 已核验或明确标 unverified，未把候选当结论。
- [ ] limitations/errors/degradations 如实记录缺失、解析失败与降级。
- [ ] change_log 记录骨架落盘与每次增量；重复运行结果一致（确定性排序、无随机）。
- [ ] 无编造 benchmark 分数、无预置答案。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 年份口径 + 空白排除证据 + 冲突记录（SKILL.md 第 1/3/4/6 节） |
| 使用增益 | 25 | scripts/core_impl.py 确定性抽取年份/术语/空白句/冲突句/代表作候选，减少重复检索与手工补年份；patch 增量合并避免整文件重写 |
| 创新增益 | 15 | 双向证据索引（claim↔evidence↔原文行）、quote_hash/span/source_kind/retrieval_note 细粒度溯源、时间窗自动划分与拐点候选、代表作按 kind 选取 |
| 可靠体验 | 10 | 空输入/坏 JSON/坏编码/年份缺失/歧义/文献爆炸/PDF 不可读的降级策略（SKILL.md 第 8 节）；selftest.py 三组合成输入回归 |
| 生态贡献 | 10 | 文档、契约字典、自检清单、可复算 CSV 表、patch 协议、引用即用脚本（本包全部文件） |

## 3. 深度分析四步的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉/正文），再统计；禁止无证据猜年份。区分“单年波动”与“跨 ≥2 年的拐点”。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体/同名异义），再判 emergence/merge/split/decline/terminology_drift；每类事件写 cause 与证据。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 coverage_absence，可排除的写 open_question；method_failure/unresolved_conflict 须有原文证据。

## 4. 证据溯源细粒度结构（本版变体）

evidence_map.items 每条的字段含义：

- `id`：全局唯一，E001 起。
- `doc_id` / `locator` / `source_file`：来源文档与 `materials/mat_XXX.txt#Lm-Ln` 定位。
- `span`：`{start_line,end_line}` 机器可读区间，供复算与高亮。
- `quote`：原文逐字引用；`quote_hash`：quote 归一化后的 sha1 前 12 位，用于检测静默改写。
- `status`：reported/not_reported/source_conflict/inferred；`kind`：fact/inference/unknown/conflict。
- `confidence`：high/medium/low；`year`、`topic_tags[]`：便于按年/主题过滤。
- `used_by_claims[]`：反向指针，指向引用该证据的 claim id。
- `retrieval_note`：该证据如何被定位（检索式/关键词/人工阅读段落），支撑“可独立复算”。

evidence_map.index 提供 `by_claim / by_doc / by_status` 三种反向索引，使“结论→证据→原文行”与“原文行→证据→结论”双向可查。

## 5. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selfcheck.json` 定位 FAIL 项；结构错误用 patch 修复后再跑 `scripts/main.py` 合并。
- 若骨架已落盘，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：确认 `/app/output/result.json` 骨架存在 → 逐类写 `/app/work/patches/<类别>.json` → `python3 -B scripts/main.py` 合并 + 复算索引 → 读 selfcheck.json 修复 → 定稿 status。
- 本地回归：`python3 -B scripts/selftest.py`（三组合成输入全 PASS）。

## 6. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选，selfcheck.ok=true。
- `partial`：产物可打开但不完整，或部分结论待人工复核（默认中间态）。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复（如无 case.json 也无 task.md）。
