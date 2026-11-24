# 降级与诚实标注规则（degradation_rules）

原则：**缺什么就写什么，绝不把"没读到"写成"已读到"，绝不把"没写"写成"满足"。** 状态要诚实，进程要稳定。

## 1. 状态判定（status）

| 情形 | status |
|---|---|
| 输入目录无任何可读文件 | `insufficient_evidence`，列出全部缺口 |
| `case.json` 缺失或 JSON 无法解析 | `input_error`，记入 `errors`，继续处理其余材料 |
| 材料部分缺失、但仍能完成部分审查 | `partial`，在 `uncertainty`/`pending_human` 列缺口 |
| 七类审查完成、证据可复核 | `ok` |

## 2. 逐类降级矩阵

| 场景 | 处置 | 字段 |
|---|---|---|
| 空输入目录 | 写合法骨架，状态 insufficient_evidence，退出码 0 | `uncertainty`、`pending_human` |
| 坏 JSON / 坏编码 | 捕获并记录，继续处理其余文件 | `errors`、`degradations` |
| 单文件超 2MB | 跳过并记录，不崩溃 | `degradations` |
| 缺软件（如 PDF 解析不可用） | 标注"该证据未能读取"，不得假装已读 | `degradations` |
| 伦理/安全未经审批 | `unconfirmed` + `needs_expert=true` | `confirmation.ethics`/`safety` |
| 设备可用性未确认 | `unconfirmed`，写明需谁确认 | `confirmation.equipment_availability` |
| 方案自相矛盾 | 两处都记录，标 `source_conflict` | 对应审查项 notes |
| 写盘失败 | 降级到最小合法 JSON，退出码 0 | 不影响 result.json 合法性 |

## 3. 最小合法产物

任何情况下 `/app/output/result.json` 都必须能被 `json.load` 解析，且至少含 `case_id` 与 `status`。若中途崩溃，`scripts/main.py` 已先落盘的骨架即为最小合法产物；后续 S1–S5 每一步结束都重新保存，只做增量改进。

## 4. 不编造清单

- 不虚构引用、DOI、数值、价格、审批状态。
- 缺失写 `pending_human`、`missing`、`not_reported`，不用记忆补全。
- 不冒充评测分数、不伪造运行结果。
- PDF 文本未成功提取时，证据定位只写到 `sources/<name>.pdf#p<页>`，quote 留待人工核，不自行转写。

## 5. 诚实标注的粒度

每条降级都要写三件事：

1. **影响哪条结论**（不笼统写"可能有影响"）。
2. **为什么降级**（缺文件、缺软件、缺数据，指明具体对象）。
3. **如何解除**（需要谁、补什么材料、大约多久）。

三件事写不全时，把该项标 `unconfirmed`，不得降级为"已满足"。

## 6. 检查项与字段映射

| 降级来源 | 写入字段 | 是否影响 status |
|---|---|---|
| 输入目录为空 | `uncertainty`、`pending_human` | 是 → insufficient_evidence |
| case.json 坏 JSON | `errors` | 是 → input_error |
| 单个材料超限/坏编码 | `degradations` | 否（partial 继续） |
| PDF 未提取 | `degradations`、对应 evidence 的 notes | 否 |
| 伦理/安全未定 | `confirmation.ethics`/`safety`、`pending_human` | 否（保留 partial/ok） |
| 设备未确认 | `confirmation.equipment_availability` | 否 |
| 方案自相矛盾 | 审查项 notes 标 `source_conflict` | 否 |

## 7. 复核记录要求

`change_log` 至少记录：骨架落盘（skeleton）、七类回填（review）、算术校验（arithmetic）、自检收口（selfcheck）。每条含 `step`、`action`、`reason`，便于"同一方案换人复跑得到可比较结论"。`runtime` 只记录平台可观测的请求数与秒数，不编造。

## 8. 与分层门校验的对应

| 门 | 校验对象 | 失败时的处置 |
|---|---|---|
| G1 契约回填 | output_contract 全字段是否在 result.json | 逐个补键（缺值给 null/[]），不改名 |
| G2 结构 | 顶层键 + 七类维度 + experiment_plan 子键 | 按骨架补齐 |
| G3 证据 | ok/conditional/blocked/conflict 的 locator+quote | 回原文截取逐字子句；PDF 无法校验则人工核 |
| G4 算术 | time_cost/parameter_ranges 是否有算式 | 用 core_impl 函数重算并写 formula |
| G5 覆盖 | 七类是否各有判断 | 为空白维度补 ≥1 条证据化判断 |
| G6 状态 | status 与 blocked 项是否矛盾 | 复核整体结论，必要时保留 partial |

自检 FAIL 不终止进程，但必须在 S5 逐条修正后才把 status 收口为 `ok`；无法修正的保留 `partial` 并在 `uncertainty` 说明。

## 9. 输入变异兼容

固定案例之间键名与嵌套层级可能不同，`scripts/main.py` 按"发现而非假设"处理：

| 变异 | 处理 |
|---|---|
| `case_id` 命名为 `id`/`challenge_id`/`armiId` | 依次尝试，取首个非空值 |
| 文件名为 `case.jsonl` | 按行解析，取首个合法 JSON 对象 |
| 无 `output_contract` | 跳过契约回填，`contract_keys=0`，不报错 |
| `plans` 为单个对象而非数组 | IR 中统一包成单元素列表 |
| 材料放在 `materials/`、`sources/`、`notes.md` | 递归扫描，按扩展名判定文本/二进制 |
| 方案字段为中文键（研究目标/设备/样本） | `_FIELD_HINTS` 同时匹配中英文键名 |
| 数值写成字符串（"2h"、"22℃"） | 用 `parse_duration`/数值转换，失败记 `unparseable` |
| 同一指标多处出现 | 全部保留，冲突标 `source_conflict` |

任何变异都不得导致进程非零退出；无法识别的键保留在 `case_record.json` 快照中，供 Agent 人工判读。

## 10. 可编辑报告（review_report.md）要求

`/app/output/review_report.md` 与 `result.json` **同源**，面向人工复核，逐条可回溯。建议结构：

1. 案例标识与状态：`case_id`、`status`、一句话总结论。
2. 研究问题与假设：从 `experiment_plan` 抽取，逐条列出。
3. 七类审查表：每类一张表，列 `id / claim / verdict / conditions / evidence.locator`。
4. 算术校验明细：`formula` 与结果（时间/区间/样本/成本）。
5. 方案比较：多版候选方案维度对照与推荐理由。
6. 确认项与待人工项：`confirmation.*` 与 `pending_human`，逐条写明"需谁、确认什么、约多久"。
7. 证据定位索引：所有 `locator` 去重列出，人工可点回原文。

要求：

- 每条判断后附 `locator`；报告不得出现 result.json 中不存在的结论。
- 改 `result.json` 必须同步改报告，反之亦然；最终两者字段一致。
- 报告是 Markdown，可直接编辑；不得用图片或二进制替代。
- 材料不足时，报告"缺口清单"列在 `pending_human` 对应小节，不省略。
