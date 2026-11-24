# 自检清单与评分维度映射（references/checklist.md）

提交前逐条勾选；`scripts/selftest.py` 自动核验结构类条目，结果写 `/app/work/selftest.json`。

## 1. 自检清单（勾选）

- [ ] result.json 合法 JSON，顶层字段覆盖 output_contract 全部键且类型合规（`selftest C01–C03`）。
- [ ] `contract_alignment` 对账表覆盖全部契约键，`value_status` 无隐式 `not_reported` 漏写。
- [ ] case_id、status、固定字段齐全（`selftest R01–R04`）。
- [ ] 四类产物非空且可打开：年度计数、演化阶段与依据、各阶段代表作与口径、空白排除证据（`selftest P01–P04`）。
- [ ] 年份口径写于 `protocol.date_definition_note` 与 `time_trend.unit_note`（在线发表/见刊/预印本）。
- [ ] 趋势判断有年度分布支撑，离散波动与拐点已区分。
- [ ] 主题演化写清阶段划分依据（何时、因何），术语漂移/同名异义已标注。
- [ ] 代表作覆盖各阶段且 criteria 可说明，不只按被引排序，含长尾方法首创。
- [ ] 空白项均写 exclusion_evidence，区分 coverage_absence 与 open_question。
- [ ] 每个 claim 经 evidence_ids 回链 evidence_map；quote 为原文逐字（`selftest Q01`）。
- [ ] 冲突证据均记录于 conflicts，未静默择一。
- [ ] change_log 含骨架落盘记录且 revision 单调递增（`selftest L01–L03`）。
- [ ] 无编造 benchmark score、无预置答案、无白板字段（`selftest F01`）。
- [ ] limitations/errors/degradations 如实记录缺失、解析失败与降级。
- [ ] 无法确认项标 `not_reported`/`inferred` 或入 `pending_human`，不凭空补结论。

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链 + 年份口径 + 空白排除证据 + `contract_alignment` 对账表（SKILL.md 第 1/3/5 节） |
| 使用增益 | 25 | `main.py` 一键 prepare/analyze/commit/finalize，自动补年份统计、术语频率、候选句与契约对齐，减少手工补写（scripts/） |
| 创新增益 | 15 | 契约类型推断 + 逐字段对齐 + 对账表 + 别名映射 + 状态枚举自适应，是基础 Agent 不具备的能力（contract.md 第 2 节） |
| 可靠体验 | 10 | 空输入/坏 JSON/契约缺失/年份缺失/歧义/文献爆炸/缺软件的分级降级（SKILL.md 第 7 节） |
| 生态贡献 | 10 | 字段字典、自检清单、可复算 CSV、契约对齐对账表、`selftest.py --demo`（本包全部文件） |

## 3. 深度分析四步质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；禁止无证据猜年份。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体），再判出现、合并、分裂、衰退；每类事件写 cause 与证据。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 `coverage_absence`，可排除的写 `open_question`。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selftest.json` 定位 FAIL 项；结构错误手工修复 result.json，再复跑 `selftest.py`。
- 若 `prepare` 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；把失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/main.py prepare` → `python3 scripts/main.py analyze` → 手工修复/`commit` 增量 → `python3 scripts/main.py finalize` → `python3 scripts/selftest.py`。
- 本地回归：`python3 scripts/selftest.py --demo` 跑良构/空输入/坏契约三组合成输入，三组全 PASS 才可交付。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选，selftest 无 FAIL。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注。
- `input_error`：输入契约不可解析且无法恢复。
