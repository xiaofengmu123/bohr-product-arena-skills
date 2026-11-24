# 输出契约与确定性规范

本文定义 result.json 的字段 schema、枚举取值、证据溯源格式与确定性保证，供 Agent 深度分析时照此填写，也供 `scripts/selftest.py` 对照校验。字段名与本文一致，未知键不删。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 取 case.json 的 case_id/id/challenge_id，缺失取 task.md 的 challenge_id，再无则 `"unknown"` |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，诚实反映完成度 |
| `research_question` | object | `{question, scope, sub_questions[], domain_note}`，问题拆为可核对子问题 |
| `protocol` | object | `{inclusion_criteria[], exclusion_criteria[], search_scope, date_definition_note}` |
| `time_trend` | object | `{unit_note, distribution[], windows[], claims[]}` |
| `topic_evolution` | object | `{stage_divisions[], term_mapping[], events[], claims[]}` |
| `representative_works` | object | `{criteria[], stages[]}` |
| `research_gaps` | object | `{gaps[], excluded_not_gaps[]}` |
| `evidence_map` | array | 每条见第 3 节 |
| `conflicts` | array | `{items[], resolution}`，冲突不隐藏、不静默择一 |
| `sources` | array | 每条 `{id, path, work_path, encoding, size_bytes, sha256, kind, year, title, citations, text_status}` |
| `uncertainty` | array | 无法确定项、归并口径、低置信结论 |
| `pending_human` | array | 需人工核验的环节（如年份缺失、主题歧义） |
| `change_log` | array | `{time, action, reason, before, after}`，time 为逻辑时钟 T+NNNs |
| `limitations` / `errors` / `degradations` | array | 缺失材料、解析失败、降级采样如实记录 |
| `runtime` | object | 预算、逻辑时钟说明、pipeline_version |
| `input_fingerprint` | string | 所有输入文件（路径排序）的相对路径+SHA-256 的摘要 |
| `determinism` | object | `{canonical_sha256, note}`，见第 4 节 |

## 2. 子结构枚举

- `time_trend.distribution[]`：`{year, count, doc_ids[]}`；`windows[]`：`{start, end, years[], doc_count, basis}`；`claims[]`：`{claim, support, evidence_ids[]}`，support 取 `reported|not_reported|inferred`。
- `topic_evolution.stage_divisions[]`：`{stage, period, basis, terms[]}`；`term_mapping[]`：`{canonical, variants[], first_year, last_year}`；`events[]`：`{type, term, period, cause, evidence_ids[]}`，type 取 `emergence|merge|split|decline|terminology_drift`。
- `representative_works.stages[]`：`{stage, period, works[]}`；`works[]`：`{doc_id, title, year, rationale, kind}`，kind 取 `origin|method|milestone|benchmark`。
- `research_gaps.gaps[]`：`{id, type, description, exclusion_evidence, evidence_ids[]}`，type 取 `open_question|method_failure|unresolved_conflict|coverage_absence`。
- `evidence_map[]` 的 `status` 取 `reported|not_reported|source_conflict|inferred`，`confidence` 取 `high|medium|low`。

## 3. 证据溯源格式

- 每条 evidence：`{id(E001起), claim, locator, quote, source_file, status, confidence, machine?}`。
- `locator` 形如 `materials/paper.txt#L12-L15`（相对 /input 的原文路径 + 行号区间）。
- `quote` 为原文逐字引用（仅去除首尾空白，不改写、不转述）；`status=reported` 时才允许带非空 quote。
- 关键结论一律经 `evidence_ids[]` 回链 evidence_map；无法定位的 quote 改为精确引用或置 `status=not_reported` 并写入 uncertainty。
- 冲突证据两方都记录并标 `source_conflict`，在 `conflicts` 里给出分辨率或“未决”。

## 4. 确定性规范（本版侧重）

1. **逻辑时钟**：不记录真实墙钟时间；`change_log.time` 用固定 `T+NNNs`（如 T+000s=prepare、T+180s=analyze）。真实运行时长由平台记录。
2. **确定性序列化**：所有 JSON 用 `json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)`；目录遍历、dict 迭代一律排序；CSV 用固定行序 + `lineterminator="\n"`。
3. **确定性采样**：文献量超上限（单文件 >2MB、材料 >200 篇）按路径排序取前 N，写入 degradations，不随机采样。
4. **输入指纹**：`input_fingerprint` 为全部输入文件的相对路径+SHA-256 摘要；两次运行输入不变则指纹不变。
5. **机械侧车**：`analyze.py` 把机械产物写 `work/determinism/mechanical.json` 与 `mechanical.sha256`；`selftest.py --determinism` 复跑 prepare+analyze 到临时目录并比对 SHA-256，证明“同输入两次运行机械产物一致”。
6. **canonical_sha256**：对 result.json 确定性序列化、并把 `determinism.canonical_sha256` 临时置空后取 SHA-256，再回填；用于事后校验产物未被意外改动。

## 5. 深度分析对机械候选的处理规则

- 机械候选标 `machine=true`、`confidence=low`；Agent 核验后：确认的去掉 `machine` 并升 `confidence`，写 `evidence_ids`；无法确认的标 `machine=true` 并写入 uncertainty；未核验的入 pending_human。
- 禁止把检索前 N 条当前沿、把关键词热度当趋势、把换说法当新兴主题、把总被引当代表作、把“没检索到”当空白。
- 空白必须写 `exclusion_evidence`（检索式 + 排除条件 + 反例排查），区分证据排除的空白与检索遗漏。
