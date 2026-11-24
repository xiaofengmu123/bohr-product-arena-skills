# 严重度分级、类型、置信度与不确定性分类

本文件是 finding 定级与 `uncertainty / pending_review / limitations` 分类的唯一依据。执行前必读。

## 1. 严重度（severity）：三问判定，禁止一律"中"

每条 finding 的 `reasoning` 必须逐一回答三问，并据此定级：

| 级别 | 判定条件（任一即够，取最重） |
|---|---|
| `critical` | 直接推翻/否定主结论，或方法根本回答不了研究问题，且难以靠改措辞修正 |
| `major` | 影响主结论的强度或可靠性，但可修正（补数据/补方法/降结论强度） |
| `minor` | 不影响主结论，但影响某条次级结论、口径一致性或可复现性细节 |
| `advisory` | 非缺陷，属可改进的建议（补报告细节、统一表述） |

三问：
1. **是否影响主结论**（是→升 critical/major）；
2. **是否可修正**（不可→升 critical；可→major/minor）；
3. **是否影响可复现性**（是→至少 major/minor）。

`reasoning` 写成一句话链，如："影响主结论强度（是）；可修正（补对照统计量）；影响可复现（缺口径）→ major"。

## 2. 类型（type）：与六维对应

- `factual_error` 事实错误（维度 1）
- `unsupported_citation` 引用不支持所引观点（维度 2）
- `method_mismatch` 方法回答不了研究问题（维度 3）
- `statistical_misreport` 统计口径与结论方向/描述不一致（维度 4）
- `overclaim` 结论超出数据/证据强度（跨 1/2/4/5，取最贴切）
- `argument_gap` 推理跳跃/隐含假设/替代解释未排除（维度 5）
- `causal_overreach` 相关性与因果性混用（维度 4/5）
- `commitment_unfulfilled` 引言承诺未交付（维度 7）
- `inconsistency` 摘要/正文/图表/口径前后不一致（维度 7）
- `norm_issue` 学术规范问题（维度 6）
- `advisory` 建议性意见（非缺陷）

## 3. 状态与置信度（status / confidence）

| status | 含义 | 何时用 |
|---|---|---|
| `confirmed` | 已确认成立 | 有原文直接证据且无歧义 |
| `probable` | 很可能成立 | 证据较强但需补充外部材料确认 |
| `pending_review` | 待复核，暂不定性 | 缺外部全文/缺数据/原文歧义 |
| `ruled_out` | 排除 | 核对后确认不成立（保留记录，防重复报） |

`confidence` 三档：`high`（直接证据）/ `medium`（间接推断）/ `low`（弱信号或依赖外部未核验信息）。
原则：**证据强度不足时降级，不升级**；`pending_review` 默认 `low` 或 `medium`。

## 4. 不确定性分类（uncertainty / pending_review / limitations 共用）

三个数组同构，字段：`id, scope, kind, statement, reason, impact, resolution`。

| kind | 含义 | 典型去向 |
|---|---|---|
| `missing_material` | 材料/章节缺失 | `limitations`（已知缺）+ `pending_review`（影响某条结论） |
| `truncation` | 长文截断未读完 | `limitations`，注明"未宣称全文已查" |
| `external_source_unverified` | 引用/外部事实无法核验 | `pending_review`，写"需外部核对" |
| `ambiguity` | 原文表述歧义，无法唯一解读 | `uncertainty` |
| `source_conflict` | 两个来源对同一指标不一致 | `uncertainty`，两个都记 |
| `no_text_layer` | PDF 无文本层/扫描版 | `degradations`（脚本层）+ `limitations`（审查范围） |
| `budget_exhausted` | 时间/请求预算用尽 | `limitations`，如实写未查完的部分 |

区分原则：
- **`uncertainty`**：影响结论置信度，但尚可给出初步判断（如 `probable`）。
- **`pending_review`**：无法定性、必须人工或外部核对的条目。
- **`limitations`**：审查本身的范围/材料/预算限制（整场审查的边界）。
- 三者都可被 finding / coverage 的 `reason` 引用（写 id，如 `U001`、`PR002`、`L003`）。

## 5. 落账规则

1. 每个 `partial/skipped` 的 coverage 维度必须有 `note` 或 `reason`。
2. `status=insufficient_evidence` 时 `limitations` 必须非空。
3. 每个 `pending_review` 条目必须能追溯到某条 finding 或某条 claim。
4. `errors` 只记进程级异常（解析失败、读盘失败）；`degradations` 只记降级处理（坏编码、PDF 无文本层、超长截断、工具缺失）。
5. 不重复：同一事项只进一个数组（`uncertainty` / `pending_review` / `limitations` 三选一），finding 里引用其 id。
