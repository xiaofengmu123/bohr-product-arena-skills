# 自检清单与评分维度映射

提交前逐条勾选。每条对应评分维度；`scripts/selftest.py --check` 会把这些检查项自动写成
`/app/work/selfcheck.json`（C01–C15），`--determinism` 追加确定性核验。人工勾选与脚本核验共用同一份清单，保证口径一致。

## 1. 自检清单（逐条可勾选）

- [ ] C01 result.json 为合法 JSON 且可加载（任务完成度）
- [ ] C02 case_id 存在且非空（任务完成度）
- [ ] C03 output_contract 全部字段原样回填，不改名、不删未知键（结果完整性）
- [ ] C04 status 取值合法：ok/partial/insufficient_evidence/input_error（可靠体验）
- [ ] C05 四类可检查产物字段齐备：time_trend / topic_evolution / representative_works / research_gaps（实际效果）
- [ ] C06 time_trend.distribution 有年度计数；为空时 unit_note 说明原因（结果正确性）
- [ ] C07 protocol.date_definition_note 写明年份口径（在线发表/见刊/预印本）（结果正确性）
- [ ] C08 代表作 criteria 覆盖多口径（origin/method/milestone/benchmark），非仅被引排序（结果正确性）
- [ ] C09 每个空白有 exclusion_evidence（检索式 + 排除条件 + 反例排查），区分 open_question 与 coverage_absence（结果正确性）
- [ ] C10 evidence_map 每条非空 quote 可逐字定位到原文（结果可核验性）
- [ ] C11 冲突证据均记录于 conflicts，未静默择一（结果完整性）
- [ ] C12 change_log 非空，记录骨架落盘与每次增量（可核验性/稳定性）
- [ ] C13 无编造 benchmark score / 白板分数 / 预置答案（可靠体验）
- [ ] C14 status 与四类产物一致；machine=true 候选已核验或入 uncertainty（可靠体验）
- [ ] C15 确定性侧车存在：work/determinism/mechanical.sha256 + input_fingerprint；`--determinism` 复跑一致（运行稳定性）

## 2. 评分维度对应

| 评分维度 | 分值 | 本 Skill 的对应做法 |
|-|-|-|
| 实际效果 | 40 | 四类产物结构 + 证据回链（locator+逐字 quote）+ 年份口径 + 空白排除证据（C05–C11） |
| 使用增益 | 25 | prepare/analyze 确定性机械抽取年份、年度分布、术语首末年份、空白/冲突候选、代表作候选，减少重复检索与手工补年份（scripts/） |
| 创新增益 | 15 | 确定性时间窗划分、年份三层归一（metadata→header→body）、术语首末年份、代表作按 kind 选取、空白候选句（analyze.py） |
| 可靠体验 | 10 | 空输入/坏 JSON/年份缺失/歧义/文献爆炸/冲突的降级策略 + 确定性复跑核验（SKILL.md 第 6 节、selftest.py） |
| 生态贡献 | 10 | 文档、契约字典、可勾选清单、可复算 CSV、确定性侧车（本包全部文件） |

## 3. 四步深度分析的质量门槛

1. **time_trend**：先确认每个 doc 的年份来源（metadata 优先，其次题录/DOI/PDF 页眉），再统计；离散波动与真实拐点分开说，禁止无证据猜年份。
2. **topic_evolution**：对 term_mapping 做人工归并（同义词/变体/同名异义），再判出现、合并、分裂、衰退、术语漂移；每类事件写 cause 与证据。
3. **representative_works**：按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，避免只取高被引，覆盖长尾方法首创。
4. **research_gaps**：每个空白写检索式 + 排除条件 + 反例排查；无法排除的写 coverage_absence，可排除的写 open_question。

## 4. 失败定位与恢复

- 任一脚本失败：读 `/app/work/selfcheck.json` 定位 FAIL 项；结构错误手工修复 result.json，再复跑 selftest。
- 若 prepare.py 已落盘骨架，即使后续失败，result.json 仍是合法 partial 结果；失败原因写入 errors/limitations。
- 恢复顺序：`python3 scripts/prepare.py --force` → `python3 scripts/analyze.py` → 手工修复 → `python3 scripts/selftest.py --check --determinism`。

## 5. 判定 status 的规则

- `ok`：四类产物齐全，关键 claim 均有证据回链，无未核验的 machine 候选，selftest 无 FAIL。
- `partial`：产物可打开但不完整，或部分结论待人工复核。
- `insufficient_evidence`：材料不足以支撑趋势/空白判断，如实标注，不编造。
- `input_error`：输入契约不可解析且无法恢复。
