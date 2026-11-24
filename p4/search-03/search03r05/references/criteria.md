# 判定口径（references/criteria.md）

本文件是筛选与排序的判定基准。**若输入的 case.json 自带纳入/排除标准与打分口径，
以输入标准为最高优先级，本文件仅作缺省补充。** 任何一条判断都必须能回指输入材料
（locator + 逐字 quote），不得由脚本或文件名推断补造。

## 1. 三级相关性（relevance）

- **direct（直接相关）**：研究问题、研究对象/人群、研究方法与结局（或输入约束）
  与科研问题基本一致，可直接用于回答该问题。凭标题关键词命中但研究对象/设计
  不匹配的，**不得**判为 direct。
- **indirect（间接相关）**：只满足部分条件：同一疾病/方法但人群或结局不同；
  综述、方法学、背景机制、相邻人群、上游证据等，可用于支撑但非直接证据。
- **irrelevant（不相关）**：研究问题、对象或方法明显偏离；命中关键词纯属词面
  巧合，或属于被排除的领域（如动物研究在仅限人群的问题中）。
- **unknown（无法判断）**：摘要/元数据缺失，材料不足以支撑判断，**不得猜测**；
  此时 tier 通常为 pending，confidence=low，并在 `uncertainty` 中说明缺什么。

判断顺序（固定，保证可复核）：先对研究对象/人群，再对方法/设计，最后对结局与
时间范围。任一层级不匹配，相关性最多到 indirect。

## 2. 三档分层（tier）

- **retain（保留）**：relevance ∈ {direct, indirect} 且置信度足以进入人工精读；
  关键 direct 文献必须在此区且靠前。
- **pending（待定）**：摘要信息不足、版本与正式发表冲突待裁定、或 relevance=
  indirect 但价值尚需复核。**该区应尽量小**（降人工成本），凡能据材料判定者
  不迁入 pending。
- **exclude（排除）**：relevance=irrelevant 且理由充分。**exclude 只放无关文献**，
  不得把间接相关或信息不足的文献误排进 exclude。

## 3. 优先级打分（priority_score，0–1）

分数回答"这条被人工看到的价值有多高"，而非单纯词面相似度；由 Agent 依据材料给出，
脚本的 `base_score` 仅是词面先验，**不得**当最终分数。参照区间：

- 0.80–1.00：direct、方法/人群/结局全匹配、原始研究，解决核心问题的关键证据。
- 0.55–0.79：direct 但有明显局限（样本小、单中心、早期阶段）；或高价值 indirect。
- 0.25–0.54：indirect 背景/方法参考，或 direct 但摘要信息不足。
- 0.00–0.24：irrelevant / 勉强沾边，应进 exclude。

同分处理（平局键，固定顺序）：`priority_score desc → evidence_completeness desc →
year desc → norm_title asc → record_id asc`。`evidence_completeness` =
有 locator+quote 的证据条数。相同输入两次运行结果必须一致（全流程确定性）。

## 4. 去重与版本冲突裁定

- 聚类判据：规范化标题等同、DOI 等同（忽略大小写与 `doi.org/` 前缀）、
  "第一作者姓 + 标题前 40 字符"等同——三者命中其一即归入同一 `dedupe_groups`。
- 裁定规则：同一聚类内，**正式发表版本优先于预印本**；同一研究的后续更新
  （更完整样本/更长随访/最终结果）优先于早期版本；无法从材料确认孰为正式版时，
  保留二者为 pending，`conflict` 记 `version_conflict` 并升人工。
- 每个成员在 `results[i].dedupe` 标 `primary_candidate` 或 `duplicate_candidate`，
  `dedupe.decision` 写明"保留正式版 / 合并 / 二者皆保留"及依据。

## 5. 冲突处理

- `annotation_conflict`：同一文献两处标注/两来源相关性不一致 → 两条都记录，
  标 `source_conflict`，取更保守档（如 direct/indirect 之间取 indirect），并进
  uncertainty。
- `metadata_missing`：缺摘要/年份/DOI → 字段填 null，relevance=unknown、
  tier=pending、confidence=low，不猜测。
- `duplicate_report`：同一研究多篇报道 → 归入 dedupe_groups 裁定，见第 4 节。
- 任何裁定写进 `results[i].conflict`，并同步写 `uncertainty`。

## 6. 证据要求

- 每条保留/排除决定必须有 `reasons`（逐条理由，说明命中哪条纳排标准）；关键
  （retain 且 direct/indirect）条目必须有 `evidence`，每项含 `locator`（如
  `case.json#papers[0].abstract` 或 `materials/doc.pdf#p3`）与逐字 `quote`。
- `criteria_matched` 逐条列出命中的纳入/排除标准原文要点，便于复核。
- 无据不写：无法定位的数值/引用/结论写 `pending_human` 或 `not_reported`。