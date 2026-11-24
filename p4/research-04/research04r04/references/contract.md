# 输出契约与证据/降级模型

本文档是 `SKILL.md` 第 3、7 节的权威扩展。脚本 `scripts/core_impl.py` 据此做确定性抽取与校验，Agent 据此逐条核验。字段名一经约定不得改名，缺值给 `null`/`[]`，未知键保留。

## 1. result.json 固定字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取 `case.json` 的 `case_id`/`id`/`challenge_id`，再取 `task.md` 的 `challenge_id`，否则 `"unknown"` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，见第 4 节降级阶梯 |
| `output_contract` | object | 若 `case.json` 含 `output_contract`，**原样回填全部字段**（缺值 `null`/`[]`，不改名、不删键）；无则 `{}` |
| `research_question` | object | `{question, scope, sub_questions[], domain_note}`，问题须拆为可核对子问题 |
| `protocol` | object | `{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}`，年份口径（在线发表/见刊/预印本）必须写明 |
| `time_trend` | object | `{unit_note, distribution[]({year,count,doc_ids[]}), windows[], claims[]}`；`claims[].support` 取 `reported\|not_reported\|inferred` |
| `topic_evolution` | object | `{stage_divisions[], term_mapping[], events[], claims[]}`；`events[].type` 取 `emergence\|merge\|split\|decline\|terminology_drift` |
| `representative_works` | object | `{criteria[], stages[]({stage,period,works[]({doc_id,title,year,rationale,kind})})}`；`kind` 取 `origin\|method\|milestone\|benchmark` |
| `research_gaps` | object | `{gaps[]({id,type,description,exclusion_evidence,counter_example_check,evidence_ids[]}), excluded_not_gaps[]}`；`type` 取 `open_question\|method_failure\|unresolved_conflict\|coverage_absence` |
| `evidence_map` | array | 每条 `{id(E001 起；机械候选 M001 起),claim,locator,quote,source_file,status,confidence,provenance}`；`status` 取 `reported\|not_reported\|source_conflict\|inferred`，`quote` 为原文逐字引用 |
| `conflicts` | array | `{items[], resolution}`；冲突证据两方都记录，不静默择一 |
| `coverage` | object | `{evidence_excluded[], search_omissions[], sample_boundary}`：显式区分「证据排除的空白」与「检索遗漏」 |
| `sources` | array | `{id,path,type,note}` |
| `uncertainty` | array | 每条 `{id(U001 起),kind,location,description,impact,mitigation}`；`kind` 见第 3 节 |
| `pending_human` | array | `{item,reason}`，无法核验或需人工补全项 |
| `change_log` | array | `{time,action,reason,before,after}`，含骨架落盘与每次增量 |
| `limitations` | array | `{id(L001 起),kind,description,evidence}`，如实列出缺失/降级/未核验类别 |
| `errors` | array | `{component,message,recovered}` |
| `degradations` | array | `{trigger,file,action,status}` |
| `runtime` | object | `{seconds,model_requests,completed_steps[],script_version}`；**不写任何 benchmark/白板/分数** |

同时产出可编辑主产物 `report.md`、证据映射 `evidence_map.json`、可复算表 `tables/documents.csv`、`tables/year_distribution.csv`、`tables/term_mapping.csv`。机械基线全部标注 `provenance="machine"`。

## 2. 三层证据边界 + 四级置信度（本版核心设计）

每条结论/证据必须先定「边界」，再给「置信度」，二者缺一不可。禁止把推断写成事实、把检索遗漏写成空白。

**三层证据边界（support / status 语义）**

| 边界 | 含义 | 落字段 | 要求 |
|-|-|-|-|
| `reported` | 材料原文直接可引，逐字片段可定位 | `support`/`status=reported` | 必须有 `locator` + 逐字 `quote`，置信度通常 `high`/`medium` |
| `inferred` | 需要跨文档归纳，材料不直接陈述 | `support`/`status=inferred` | 必须在 `claim` 或 `rationale` 写清归纳路径，置信度 `medium`/`low` |
| `not_reported` | 材料查不到，不补写 | `support`/`status=not_reported` | 置信度 `unverified`，并写入 `pending_human` 或 `uncertainty` |

**四级置信度（confidence）**

| 值 | 触发条件 |
|-|-|
| `high` | 有逐字 quote + locator，无冲突，且为直接陈述 |
| `medium` | 有 quote 但需归纳，或存在轻微口径歧义 |
| `low` | 仅间接证据 / 术语歧义 / 样本边界 / 单一来源 |
| `unverified` | 机械候选未核验，或证据缺失 |

**不越过证据扩展结论**：`not_reported` 不得升为 `reported`；`inferred` 不得改写成 `reported`。机械候选在 Agent 核验前一律 `confidence=unverified`，核验后改写 `provenance=verified`。

## 3. 不确定性账本（uncertainty[]）

每个不确定性条目必须回答「影响哪个字段」与「如何缓解」，而不是一句笼统的「存在不确定性」。

| `kind` | 触发 | 必须指向 |
|-|-|-|
| `coverage_gap` | 检索范围可能漏掉子领域/语言/预印本 | `coverage.search_omissions` + 对应 `research_gaps` |
| `year_ambiguity` | 年份为启发式推断或缺失 | `time_trend.distribution` / `unit_note` |
| `term_polysemy` | 同名异义/术语漂移 | `topic_evolution.term_mapping` / `events` |
| `source_conflict` | 两来源结论不一致 | `conflicts` + 相关 `claims[].evidence_ids` |
| `sampling_boundary` | 文献量超上限被采样 | `coverage.sample_boundary` |
| `parse_error` | PDF/编码解析失败 | `degradations` + 对应文档 |
| `retrieval_omission` | 材料整体缺失 | `research_gaps` |

## 4. 降级阶梯（status 状态机）

`status` 只取四档，由「四类产物 + 证据回链 + 未核验项」三者联合判定：

| 档 | 触发条件 | 必须同时写入 |
|-|-|-|
| `ok` | 四类产物齐全、关键 claim 均有证据回链、无未核验 machine 候选影响关键结论 | `uncertainty`（残余项）、`limitations`（残余项） |
| `partial` | 产物可打开但不完整，或部分结论待人工复核 | `limitations` 逐条列出未完成类别 |
| `insufficient_evidence` | 材料不足以支撑趋势/空白判断 | `research_gaps` 写「无法区分空白与遗漏」，不编造结论 |
| `input_error` | `case.json`/`task.md` 不可解析且无法恢复 | `errors` 记录具体解析错误 |

**降级触发器 → 强制动作**（缺一即为漏写）：

| 触发器 | status 下限 | 强制动作 |
|-|-|-|
| 检索为空/缺材料 | `insufficient_evidence` | `research_gaps` 写材料不足；`limitations` 记 `no_materials` |
| 年份缺失 | 可保持 `partial` | `time_trend.distribution=[]`、`unit_note` 写「无出版年份元数据，待人工」、`pending_human` 记录，不猜年份 |
| 主题严重歧义/同名异义 | 可保持 `partial` | `term_mapping` 记录全部变体、`uncertainty` 记 `term_polysemy`、`confidence=low` |
| 文献量爆炸 | 可保持 `partial` | 脚本按 2MB/200 文件上限采样并记 `degradations`，`coverage.sample_boundary` 写样本边界 |
| 证据冲突/权限不足/PDF 不可读 | `partial` | `source_conflict`/`degradations`/`limitations`，不静默择一、不伪造文本层 |
| 脚本失败 | 保留已落盘骨架 | `errors` 记录，`status` 如实标 `partial`/`insufficient_evidence` |

## 5. 确定性复算要求

- 机械统计（年度分布、术语频率、候选句）由脚本产生，重复运行字节一致（`selftest.py --demo` 验证）。
- `tables/*.csv` 由脚本重写，任何结论都可据此独立复算；手工结论必须回链 `evidence_map`。
- `change_log` 记录骨架落盘与每次增量修改；禁止伪造评测分数、白板、DOI、引用数或来源文本。
