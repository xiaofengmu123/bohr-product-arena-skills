# 判定口径与打分标准（criteria.md）

本文件是本 Skill 的"规则层"：Agent 逐条填 `evidence_ledger.json` 时，唯一可依据的判定口径。`scripts/core_impl.py` 会把输入自带标准编译进 `plan.json`；**输入自带标准优先级最高**，本文件是缺省口径与边界案例约定。

## 1. 相关性三级判定（relevance）

对每条候选，先读标题与摘要（必要时读材料 PDF 原文），按研究问题逐条判断。判断维度优先按题目给出的纳排标准展开，缺省为：

1. 研究对象 / 人群 / 物种是否匹配
2. 干预 / 暴露 / 方法是否匹配
3. 结局指标 / 证据类型是否匹配
4. 时间范围、可比性（对照组）、研究设计是否匹配

| relevance | 判定条件 | 常见证据 |
|-|-|-|
| `direct` | 研究对象、方法、结局、人群等**核心条件同时匹配**研究问题 | 摘要明示目标人群 + 目标干预 + 目标结局 |
| `indirect` | 部分匹配：方法相关但人群不同；人群相关但结局不同；综述/背景可作方法参考 | 摘要匹配 1–2 个维度 |
| `irrelevant` | 关键维度不匹配，或命中排除条件（动物/体外、纯方法工具、无关主题） | 摘要明示不同人群/对象或命中排除项 |
| `unknown` | 摘要/材料不足以判断（缺摘要、摘要过短、关键信息缺失） | 只有标题或摘要缺失 |

**边界约定**：
- 标题含关键词但摘要显示设计不匹配 → 不得判 `direct`，按摘要实际内容判 `indirect`/`irrelevant`。
- 综述不因"是综述"而自动排除；若覆盖同一研究问题的证据，判 `indirect`（方法/背景参考）而非 `direct`。
- 预印本与已发表版本是同一研究：内容层判同一 relevance，去重层用 `dedupe` 处理（见 §4）。
- 缺摘要/缺年份/缺 DOI：对应字段 `null`，判据不足时 `relevance=unknown`，**不猜测**。

## 2. 三档 tier（分层标签）

`tier` 由 `relevance` 经 `plan.json` 的 tier_gates 推导；Agent 填写时必须一致。

| tier | 来自 relevance | 含义 | 人工动作 |
|-|-|-|-|
| `retain` | direct | 核心证据，先读 | 复核即可采信 |
| `pending` | indirect / unknown | 方法参考或证据不足，待确认 | 快速复核是否升级/排除 |
| `exclude` | irrelevant | 命中排除条件或明显不相关 | 仅核对排除理由 |

**原则**：`exclude` 只放 `irrelevant`；间接相关与"关键但证据不足"不得误排进 exclude，保持召回。

## 3. priority_score 打分口径（0–1）

分数是"优先级"而非"相关性"：`relevance` 决定档位，`priority_score` 决定档位内先后。由 Agent 依据以下规则给分，**不是词面先验的 `base_score`**。

- 起点按 tier：direct 0.7–1.0；indirect 0.4–0.79；irrelevant 0.0–0.3。
- 在同一 tier 内加分：证据等级（RCT/系统综述 > 队列 > 病例/综述 > 观点）、样本规模与目标人群匹配度、结局完整、年份接近、元数据完整。
- 减分：预印本未正式发表、元数据缺失、摘要信息不足、存在标注分歧。
- 分段含义（`plan.json#score_bands`）：
  - 0.8–1.0 核心证据，先读
  - 0.5–0.79 方法/背景参考
  - 0.0–0.49 背景或应排除
- **禁止**：把 `scaffold.json` 的 `base_score` 直接当最终分数；`base_score` 仅是词面先验。

## 4. 去重与版本冲突（dedupe）

`scaffold.py` 的确定性聚类只做聚类骨架；Agent 用 `dedupe_override` 复核或修正裁定，并在 `reasons` 说明。

聚类规则（`plan.json#dedupe_rules`）：
- R1 相同非空 DOI → 同一文献
- R2 相同规范化标题 → 同一文献
- R3 去版本标记后的标题相同（preprint/published version 等）→ 版本变体

裁定规则：
- 组内选 `primary`：优先正式发表（有 venue 或 DOI 且非 preprint），其次元数据完整度，再其次 record_id。
- 变体标 `version_variant` 并在 `dedupe.primary_record_id` 指向 primary，不重复计入"不同文献"。
- 同一研究多篇报道（如 protocol + 结果 + 亚组）若标题/DOI 不同则各自保留，但互填 `conflict.note` 指向关系。

## 5. 冲突处理（conflict）

`conflict` 字段记录并保留分歧，**不静默取其一**。形态：

```json
{
  "kind": "source_conflict | version_conflict | label_disagreement | abstract_insufficient",
  "values": ["两侧逐字取值"],
  "resolution": "保留 primary / 升级 pending_human / 以正式发表为准",
  "note": "一句话理由"
}
```

- 同一指标两个来源不一致 → `source_conflict`，两个值都记录。
- 摘要不足 → `abstract_insufficient`，`relevance=unknown`、`tier=pending`、`confidence=low`。
- 标注分歧/无法裁定 → `label_disagreement`，写入 `pending_human`，两侧逐字引用。

## 6. 证据要求（硬要求）

每条关键结论必须给出证据，形态为 `{"locator": "...", "quote": "逐字引用", "criterion_id": "cXX"}`：

- `locator`：材料定位，如 `case.json#papers[3].abstract`、`materials/paper.pdf#p4`、`task.md#纳入标准`。
- `quote`：材料中的**逐字片段**，不得改写、不得概括；长度以能复核判断为限。
- `criterion_id`：命中的 `plan.json` 准则 id（无显式准则时用 `c00`）。
- 每个 `reasons[i]` 应至少对应一条 `evidence`；`relevance != unknown` 而 `evidence` 为空 → 记为 warning。

## 7. 元数据准确

- title/authors/year/venue/doi/url 与输入一致，**缺失填 `null`，不猜测、不补造**。
- authors 统一为字符串数组；year 保持数值；DOI 原样保留大小写。
- 不得依据文件名、领域标签或题目提示补造材料内容；PDF 无预抽取答案层时须实际读取。
