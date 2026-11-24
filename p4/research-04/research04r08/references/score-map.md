# 评分维度对齐与字段字典（score-map）

本文件是 SKILL.md 第 2、4 节的完整版。评分维度、分值与评分点来自题面；“流程落点/自检项/字段”是本 Skill 的设计映射。

## 1. 评分维度 → 流程 → 自检项 → 字段

| 维度 | 分值 | 评分点 | 流程落点 | 自检项 | 可核验字段 |
|-|-|-|-|-|-|
| 实际效果 | 40 | 任务完成度 15 | 第 5 步四类深度分析 | S1 | time_trend/topic_evolution/representative_works/research_gaps 非空 + report.md |
| | | 结果正确性 15 | 第 6 节质量门槛 + 第 7 节证据回链 | S2/S5 | protocol.date_definition_note、distribution、claims[].evidence_ids、criteria、gaps[].exclusion_evidence |
| | | 结果完整性 5 | 第 5 步四类顺序覆盖 | S3 | 时间跨度、stages、gap type 覆盖 |
| | | 结果可核验性 5 | 第 5 步自检 | S4 | evidence_map 逐字 quote 可定位 + tables/*.csv |
| 使用增益 | 25 | 质量增益 15 | 机械基线 → 语义核验 | S6/S7 | machine.baseline + change_log 记录候选→结论升级 |
| | | 效率增益 5 | main.py 去重复检索/补年份/统计 | S7 | scripts/main.py 存在且可运行 |
| | | 增益可验证性 5 | 对照记录 | S8 | runtime（耗时/请求数）+ change_log |
| 创新增益 | 15 | 新增能力 5 | 时间切片/术语首末年份/kind 选取 | S9 | machine.term_stats、windows、criteria |
| | | 方法改进 5 | 年份归一/术语归并/空白判据 | S10 | protocol、term_mapping、gaps[].exclusion_evidence |
| | | 创新证据 5 | 与“检索前 N 条/没检索到”基线划界 | S11 | excluded_not_gaps + coverage_absence |
| 可靠体验 | 10 | 调用易用 3 | SKILL.md 第 3/4 节 | S12 | frontmatter、相对路径 |
| | | 运行稳定 4 | 先落盘骨架 + 确定性 | S13 | 骨架先行、无随机数 |
| | | 异常处理 3 | 第 9 节降级 | S14 | limitations/errors/degradations |
| 生态贡献 | 10 | 文档完整 3 | references/ | S15 | score-map.md + checklist.md |
| | | 资源复用 4 | 脚本/词表/统计表 | S16 | 标准库脚本 + tables/*.csv |
| | | 分享价值 3 | 方法说明与边界 | S16 | 方法规则与 limitations |

`result.json.alignment` 由 selftest.py 自动回填每个维度（A1–C3）的 `{label,status,evidence}`，使“对齐”本身可被复核。

## 2. result.json 固定字段字典

- `case_id`：取 case.json 的 case_id/id/challenge_id，缺失取 task.md 的 challenge_id，再无则 `"unknown"`。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实反映完成度。
- `research_question`：`{question, scope, sub_questions[], domain_note}`；问题拆解为可核对子问题。
- `protocol`：`{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}`；纳排规则与年份口径（在线发表/见刊/预印本）。
- `time_trend`：`{unit_note, distribution[]({year,count,doc_ids[]}), windows[], claims[]({claim,support,evidence_ids[]})}`；`support` 取 `reported|not_reported|inferred`。
- `topic_evolution`：`{stage_divisions[]({stage,period,basis,terms[]}), term_mapping[]({canonical,variants[],first_year,last_year}), events[]({type,term,period,cause,evidence_ids[]}), claims[]}`；`type` 取 `emergence|merge|split|decline|terminology_drift`。
- `representative_works`：`{criteria[], stages[]({stage,period,works[]({doc_id,title,year,rationale,kind})})}`；`kind` 取 `origin|method|milestone|benchmark`。
- `research_gaps`：`{gaps[]({id,type,description,exclusion_evidence,evidence_ids[]}), excluded_not_gaps[]({candidate,reason})}`；`type` 取 `open_question|method_failure|unresolved_conflict|coverage_absence`。
- `evidence_map`：每条 `{id(E001起),claim,locator,quote,source_file,status,confidence}`；`status` 取 `reported|not_reported|source_conflict|inferred|machine_candidate`，`quote` 为原文逐字引用。
- `conflicts`：`{items[], resolution}`；冲突不隐藏、不静默择一。
- `sources`、`uncertainty[]`、`pending_human[]`、`change_log[]({time,action,reason,before,after})`、`limitations[]`、`errors[]`、`degradations[]`、`runtime{started_at,finished_at,elapsed_s,model_requests}`。
- `machine`：`{baseline, unverified[], gap_candidates[], conflict_candidates[], rep_candidates[], term_stats[], document_count}`；机械候选在语义核验前标 `machine=true`，核验后升级或注明未核验。
- `alignment`：`{A1..C3: {label,status,evidence}}`；由 selftest.py 回填。
- `output_contract_keys`：记录从 output_contract 原样回填的键名（溯源用，不删未知键）。

## 3. 方法说明（对应“创新增益”，写进 result.json 的 protocol / claims 或 method_notes）

1. **年份归一**：元数据年份优先（year/published/date 字段），其次题录/DOI/页眉年份（citation），来源与置信度逐篇记录（`year_source`/`year_confidence`）；缺失不猜，入 pending_human。
2. **时间窗划分**：先由 scripts/main.py 产出逐年年份分布，Agent 再依据分布相邻计数与材料内容给出 `windows[]` 及 `basis`（等宽/拐点/学科事件），不允许无分布支撑的任意窗。
3. **术语归并**：scripts/main.py 产出术语频率与首末年份（term_stats）；Agent 在 term_mapping 归并同义/变体，标 canonical 与 variants，再判事件类型；同名异义入 uncertainty 且 confidence=low。
4. **代表作选取**：不只用被引排序；按阶段分桶，每阶段按 kind（origin/method/milestone/benchmark）覆盖，包含长尾方法首创工作；criteria 写明入选口径。
5. **空白判定**：空白候选句（future work/limitation/unknown）仅为候选；每个空白必须写 exclusion_evidence（检索式 + 排除条件 + 反例排查）；证据能排除写 open_question，不能排除写 coverage_absence。

## 4. 枚举汇总

- status：`ok | partial | insufficient_evidence | input_error`
- support：`reported | not_reported | inferred`
- evidence status：`reported | not_reported | source_conflict | inferred | machine_candidate`
- event type：`emergence | merge | split | decline | terminology_drift`
- work kind：`origin | method | milestone | benchmark`
- gap type：`open_question | method_failure | unresolved_conflict | coverage_absence`

## 5. 依赖与复用

- 仅 Python 标准库（argparse/json/os/re/subprocess/csv/shutil/pathlib/collections/datetime）。
- 脚本为相对路径调用：`scripts/main.py`、`scripts/selftest.py`、`scripts/core_impl.py`（被前两者 import）。
- 复用顺序：`python3 scripts/main.py`（骨架+机械基线）→ Agent 语义升级 → `python3 scripts/selftest.py`（校验）。
