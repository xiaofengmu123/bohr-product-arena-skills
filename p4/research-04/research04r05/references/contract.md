# 输出契约与字段字典（references/contract.md）

本文件是 `SKILL.md` 第 3、7 节的权威扩展，定义 `result.json` 的固定字段、契约对齐规则与机械抽取口径。字段名一经约定不得改名；缺值给 `null`/`[]`，未知键保留。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取 `case.json` 的 `case_id`/`id`/`challenge_id`，再取 `task.md` 的 `challenge_id`，否则 `"unknown"` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，见第 4 节降级阶梯 |
| `research_question` | object | `{question, scope, sub_questions[], domain_note}` |
| `protocol` | object | `{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}`，年份口径必须写明 |
| `time_trend` | object | `{unit_note, distribution[]({year,count,doc_ids[]}), windows[], claims[]}` |
| `topic_evolution` | object | `{stage_divisions[], term_mapping[], events[], claims[]}` |
| `representative_works` | object | `{criteria[], stages[]({stage,period,works[]({doc_id,title,year,rationale,kind})})}` |
| `research_gaps` | object | `{gaps[]({id,type,description,exclusion_evidence,evidence_ids[]}), excluded_not_gaps[]}` |
| `evidence_map` | array | `{id,claim,locator,quote,source_file,status,confidence}` |
| `conflicts` | array | `{items[], resolution}`，冲突不隐藏、不静默择一 |
| `sources` / `uncertainty` / `pending_human` / `limitations` / `errors` / `degradations` | array | 见下 |
| `change_log` | array | `{time,action,reason,before,after}`，含骨架落盘与每次增量 |
| `runtime` | object | `{started_at, phase, finished_at?, script_version}` |
| `deliverable` | string | 主产物相对路径 `report.md` |
| `deliverables` | array[string] | 全部可编辑/可复算产物相对路径 |
| `contract_alignment` | array | 契约对齐对账表（本版核心，见第 2 节） |
| `revision` | int | 单调递增版本号 |

## 2. 契约对齐（本版核心设计）

`output_contract` 是 `case.json` 里 `{字段名: 类型描述}` 的字典。对齐规则：

1. **类型推断**：`core_impl.infer_type(desc)` 按描述关键词推断 JSON 类型——含 `数组/array/list/[]/清单` → array；含 `对象/object/dict/块` → object；含 `数值/number/int/float` → number；含 `布尔/boolean` → boolean；含 `string/字符串/文本/路径/枚举/回填` → string；否则默认 object。
2. **逐字段对齐**：对 `output_contract` 的每个键，先按别名表（`core_impl.ALIASES`）映射到分析字段，能映射就用分析值，映射不到置类型缺省值（`""`/`{}`/`[]`/`0`/`false`）。键**原样落到 result.json 顶层**，不改名、不删未知键。
3. **对账表**：`contract_alignment` 每条 `{contract_key, declared, inferred_type, source, value_status, note}`，`value_status` 取 `filled | empty | not_reported`。`not_reported` 表示"契约字段无对应分析源"，必须由深度分析或人工补全，不得假装有值。
4. **状态枚举映射**：若 `output_contract.status` 声明了不同枚举（如 `answered`），`core_impl.resolve_status_enum` 抽取并写入 `_declared_status_enum`，映射 `answered→ok`、`input_error→input_error` 等，`contract_alignment` 里记录，不静默改名。
5. **重算**：每次 `commit`/`analyze`/`finalize` 之后调用 `core_impl.re_align` 重新对齐并刷新对账表，保证顶层字段与 contract 始终一致。

## 3. 四类产物枚举

| 维度 | 字段 | 枚举 |
|-|-|-|
| claim | `claims[].support` | `reported \| not_reported \| inferred` |
| 演化事件 | `events[].type` | `emergence \| merge \| split \| decline \| terminology_drift` |
| 空白 | `gaps[].type` | `open_question \| method_failure \| unresolved_conflict \| coverage_absence` |
| 代表作 | `works[].kind` | `origin \| method \| milestone \| benchmark` |
| 证据 | `evidence_map[].status` | `reported \| not_reported \| source_conflict \| inferred` |
| 置信度 | `evidence_map[].confidence` | `high \| medium \| low \| unverified` |

## 4. 降级阶梯（status 状态机）

| 档 | 触发条件 | 必须同时写入 |
|-|-|-|
| `ok` | 四类产物齐全、关键 claim 有证据回链、无未核验 machine 候选影响关键结论 | 残余 `uncertainty`/`limitations` |
| `partial` | 产物可打开但不完整，或部分结论待人工 | `limitations` 逐条列出未完成类别 |
| `insufficient_evidence` | 材料不足以支撑趋势/空白判断 | `research_gaps` 写「无法区分空白与遗漏」 |
| `input_error` | `case.json`/`task.md` 不可解析且无法恢复 | `errors` 记录具体解析错误 |

降级触发器 → 强制动作：

| 触发器 | status 下限 | 强制动作 |
|-|-|-|
| 检索为空/缺材料 | `insufficient_evidence` | `research_gaps` 写材料不足；`limitations` 记 `no_materials` |
| 年份缺失 | 可保持 `partial` | `distribution=[]`、`unit_note` 写「无出版年份元数据，待人工」、`pending_human` 记录，不猜年份 |
| 主题严重歧义 | 可保持 `partial` | `term_mapping` 记全部变体、`uncertainty` 记 `term_polysemy`、`confidence=low` |
| 文献量爆炸 | 可保持 `partial` | 2MB/200 文件上限确定性采样并记 `degradations`、`coverage.sample_boundary` |
| 证据冲突/权限不足/PDF 不可读/缺 pdftotext | `partial` | `source_conflict`/`degradations`/`limitations`，不静默择一、不伪造文本层 |
| 契约缺失或非 dict | 可保持 `partial` | 仅产出固定字段，`contract_alignment=[]`，记 `errors` |

## 5. 确定性复算要求

- 机械统计由脚本产生，无随机、无墙钟依赖；同一 manifest/materials 重跑结果一致。
- `tables/*.csv` 由脚本重写，任何结论可据此独立复算；手工结论必须回链 `evidence_map`。
- `change_log` 记录骨架落盘与每次增量，`revision` 单调递增；禁止伪造评测分数、白板、DOI、引用数或来源文本。
