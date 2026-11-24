# 判定口径（criteria）

本文件是逐条判断的唯一口径来源；若 `case.json` 自带纳入/排除标准或相关性定义，
**以输入标准为最高优先级**，本文件仅在输入未规定处兜底。所有判断必须落在材料证据上，
不得凭标题词面或领域标签补造。

## 1. 三级相关性（relevance）

| 标签 | 判定条件 | 典型边界 |
|-|-|-|
| `direct` | 研究对象、方法/设计、人群、时间范围均匹配纳入标准，可直接回答研究问题 | 仅标题含关键词但设计不匹配 → **不是** direct |
| `indirect` | 部分匹配：方法参考、背景证据、相邻人群、部分结局，或综述可作为线索 | 综述覆盖问题但非原始研究 → indirect |
| `irrelevant` | 明确命中排除标准，或研究对象/问题不相关 | 人群为儿童/动物而标准排除 → irrelevant |
| `unknown` | 摘要/年份/DOI 缺失，或材料不足以判断 | 缺摘要 → unknown，不猜测 |

判断顺序（固定）：先查排除标准（命中即 irrelevant）→ 再查纳入标准（全命中 direct，部分命中 indirect）→ 材料不足 unknown。

## 2. 三档分层（tier）

| 档 | 判定条件 | 与 relevance 的约束 |
|-|-|-|
| `retain` | direct，且可作为核心证据/方法参考，值得优先复核 | relevance 通常为 direct（个别强 indirect 方法参考可 retain，须在 reasons 说明） |
| `pending` | indirect / unknown / 摘要不足 / 标注冲突 / 版本重复待裁定 | 拿不准一律进 pending，不降格排除 |
| `exclude` | irrelevant，明确命中排除标准 | 只放 irrelevant；indirect 不得误排进 exclude |

## 3. priority_score（0–1，保留一位有效区间即可，脚本不限精度）

- 仅在同一 tier 内比较优先级；跨 tier 由 tier 顺序决定，不跨档比分数。
- 0.85–1.00：direct 核心证据，直接回答研究问题，设计匹配、有 DOI/全文可核。
- 0.60–0.84：direct 但存在局限（样本小、非首选结局、预印本、证据仅摘要）。
- 0.30–0.59：indirect 方法/背景参考，或 unknown 但潜在相关。
- 0.00–0.29：irrelevant（进入 exclude，分数用于同档内稳定排序）。
- `base_score`（scripts/screen.py 输出的词面先验）**不得**直接当 final 分数；最终分数须结合语义判断，并在 reasons 说明。

## 4. 证据要求

- 每个 `retain` / `exclude` 决定必须给出 ≥1 条 evidence；`pending` 建议给出。
- evidence 每项含：`locator`（如 `case.json#papers[0].abstract`、`materials/paper1.pdf#p2`）+ 逐字 `quote`（材料原文，不得改写）。
- 无逐字引用时不得声称有证据；缺证据写 `pending_human` 或 `not_reported`。

## 5. 去重与版本裁定（dedupe）

- 情形 A：同一研究 preprint 与 published 版本 → 归入同一 `dedupe_groups`，published 为 `primary`，preprint 标 `duplicate`/`version`；保留 primary 的排序位置，duplicate 不重复占位。
- 情形 B：同一研究多篇报道（子分析、二次发表、结果重复）→ 归组并在 `conflict`/`dedupe` 注明关系；无法确定是否同研究 → 归 `pending` 并 `pending_human`。
- 情形 C：两来源对同一事实结论不一致（标注分歧）→ 两者都记录，标 `source_conflict`，不隐藏任一侧。
- 脚本 `screen.py` 只做确定性候选聚类（同 DOI / 同规范化标题 / 标题词集包含或高重叠）；最终 primary/duplicate 裁定由 Agent 依据材料完成。

## 6. 冲突与缺失处理

- 摘要信息不足 → `relevance=unknown`、`tier=pending`、`confidence=low`，写入 `uncertainty`。
- 年份/DOI/作者缺失 → 对应字段 `null`，不猜测、不补造。
- 纳入标准互相冲突 → 在 `uncertainty` 记录冲突标准，按"先排除、再纳入、拿不准进 pending"处理并说明。

## 7. 确定性排序键（固定，禁止改动）

`tier(asc) → priority_score(desc) → evidence_completeness(desc) → year(desc) → norm_title(asc) → record_id(asc)`

- `norm_title` = 大小写折叠、NFKC 规范化、去标点、去版本标记、压缩空白。
- 相同输入两次运行，除时间戳/运行时长外决策内容一致；`finalize.py` 强制按此键排序并重写 `rank=1..N`，`decision_fingerprint` 用于核验。
