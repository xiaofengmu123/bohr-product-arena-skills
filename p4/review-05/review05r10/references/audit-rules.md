# 判定规则与误报抑制（source-bound）

本文件是审查 Agent 的**判定层规则**。脚本只产出候选观察与反证提示（见
`evidence.json`），任何 finding 都必须由 Agent 依据本规则逐条判定后方可写入
`findings.json`。规则的目标是：**报出的每条都成立且可回查，误报与漏报同等扣分**。

## 1. 四类 finding 与四档严重度

| type | 含义 | 判定门槛 |
|---|---|---|
| `fact_error` | 事实错误：数值、单位、文献元数据、公式、图/表与正文不符 | 必须能指回原文具体片段，且与可核对来源冲突 |
| `insufficient_evidence` | 证据不足：方法/统计/伦理/数据授权交代不完整，无法判断 | 材料中确实缺该要素，而不是"评审者不知道" |
| `norm_violation` | 规范/合规问题：引用、隐私、版权、伦理、投稿要求违规 | 必须给出具体条款/指南/授权范围作为 basis |
| `suggestion` | 建议：可改进但非错误、非违规 | 明确是建议，不升格为错误或违规 |

严重度：`critical`（可能撤销/拒稿/数据安全问题）、`major`（需作者处理方能通过）、
`minor`（应修正但不影响结论）、`info`（可选优化）。**升档必须由证据支撑**：
风格差异、学科惯例、个人表述、可接受的省略，最多到 `info`/`suggestion`。

## 2. 依据绑定（basis_kind）

每条 finding 必须声明 `basis_kind`，杜绝编造条款/期刊要求/文献元数据：

| basis_kind | 要求 |
|---|---|
| `material_quote` | 依据是材料自身文本，`basis` 引原文片段 |
| `normative_clause` | 依据是规范条款/政策（如 ICMJE、COPE、GDPR、期刊指南），`basis_source` 必须写条款编号/版本/日期；未实际核对到原文的标注 `unverified` |
| `source_metadata` | 依据是文献元数据（DOI、卷期、作者），须来自检索到的原始记录 |
| `journal_guide` | 依据是目标期刊/会议投稿指南，`basis_source` 写指南版本与日期；拿不到指南原文本时不得编造条目 |
| `pending_human` | 无法由 Agent 确认，转入 `pending_human`，不写死结论 |
| `not_reported` | 材料确实未报告该信息，作为 `insufficient_evidence` 的依据 |

`basis_source` 只能写**实际读到/检索到**的内容；未核对的条款不得声称"某期刊要求……"。

## 3. 误报抑制清单（以下不算违规）

- 风格差异、学科惯例、个人表述、语言偏好。
- 同一指标不同格式/单位但数值等价。
- 片段材料未包含某声明，但该声明属于另一份材料（跨文件缺失要先确认材料边界）。
- 已获授权/已标注许可的复用（见反证提示）。
- 可接受范围内的统计表述（如未报告效应量但已给出 p 值与样本量时，只作 `suggestion`）。

**冲突信息**：同一指标两处来源不一致，两处都记录并标 `source_conflict`，不擅自取其一。

## 4. 反证检查（counter-check）

脚本在 `evidence.json` 给出 `counter_hints`：与"复用/隐私/伦理"候选同现的
**已授权/许可/脱敏/豁免**片段。凡涉及这些类别的候选，确认违规前必须：

1. 在原文本中搜索 counter_hint 及其邻近上下文；
2. 若找到反证 → 排除或降级（`counter_check.result = "contradicted"`）；
3. 若确认无反证 → `counter_check.result = "confirmed_absent"`；
4. 无法判断 → `pending_human`。

每条 finding 建议带 `counter_check: {searched: bool, result: ...}`，使"误报已排除"可复核。

## 5. 严重度标定示例（示例，非正式案例）

- 引用不存在的文献且正文论断依赖它 → `citation` / `fact_error` / `major`。
- 引文存在但作者-年份/DOI 与正文不符 → `fact_error` / `minor~major`（视影响）。
- 未报告伦理批件/知情同意，且研究明确涉及人 → `ethics` / `insufficient_evidence`（无法确认是否漏报）或 `norm_violation`（指南强制时）/ `major`。
- 图/表复用未标来源与许可 → `copyright` / `norm_violation` / `major`。
- 统计只报 p 值不报效应量/样本量 → `statistics` / `suggestion` / `minor`。
- 术语/格式风格不一致 → 不判违规，最多 `suggestion`/`info`。

## 6. 状态语义

`status` 表示**审查交付完整度**，不是"论文是否有问题"：

- `ok`：全部检查面已查、findings 已定稿、quote 可回查、无 pending_human 阻塞。
- `partial`：审查未完全收敛（部分面未查 / 有 pending_human / 有未核实项）。
- `insufficient_evidence`：无可解析材料文本，无法开展审查。
- `input_error`：case.json 缺失/不可解析或输入结构错误。
