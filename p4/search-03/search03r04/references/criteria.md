# 判定口径（references/criteria.md）

本文件是逐条判断的**唯一判定依据**；若 case.json 自带纳排标准，输入标准优先级最高，本文件只作为缺省与补充。所有判断必须能回指材料中的逐字片段（locator + quote），不得依据文件名或领域标签推断。

## 1. 三级相关性（relevance）

| 值 | 判定条件 | 边界处理 |
|-|-|-|
| `direct` | 研究问题、研究对象/人群、方法、结局/证据类型均与纳排条件实质匹配，且能指到摘要中支撑片段 | 综述与原始研究都可能是 direct，取决于问题要求；标题含关键词但研究对象/人群/设计不匹配 → **不得**判 direct |
| `indirect` | 主题相关但对象/人群/方法/时间范围部分不符，或仅提供方法、背景、可迁移证据 | 作为方法参考/背景资料保留，tier 通常 pending 或 retain（低优先级） |
| `irrelevant` | 主题、对象、方法均不匹配，或与科研问题无关的噪声 | 只有 irrelevant 才能进入 exclude |
| `unknown` | 摘要缺失/信息不足，无法判断相关性 | tier=pending，confidence=low，写 `uncertainty.abstract_insufficient`，不得猜测 |

## 2. 三档 tier

| 值 | 含义 | 进入条件 |
|-|-|-|
| `retain` | 保留，用户优先查看 | relevance ∈ {direct, indirect} 且证据充分（indirect 须标注其为方法/背景参考） |
| `pending` | 待人工复核 | relevance=unknown；摘要不足；冲突未裁定；缺关键元数据；直接相关但证据链不完整 |
| `exclude` | 排除 | **仅限** relevance=irrelevant，且排除理由可复核 |

`pending` 区尽量小；每条 pending 必须有明确的 `uncertainty` 或 `conflict` 说明升级原因。

## 3. 0–1 打分口径（priority_score）

- 语义优先，词面仅先验：`scaffold.json` 的 `base_score` 只能用于检索提示与排序参考，不得直接作最终分。
- 最终分 = 相关性强度为主 + 证据质量 + 方法/人群匹配度 + 新颖度（若纳排标准要求），按以下锚点：
  - 0.85–1.00：direct 且证据完整、方法人群匹配、元数据齐全
  - 0.65–0.84：direct 但摘要证据略欠，或 indirect 但高度可迁移
  - 0.40–0.64：indirect（方法/背景参考）或 direct 但冲突未完全裁定
  - 0.15–0.39：间接相关、信息不足、仅主题重合
  - 0.00–0.14：irrelevant
- 同一 tier 内单调：retain 内分数应 ≥ pending 内分数；exclude 内分数应最低。
- 置信度映射：high=证据逐字完整且无冲突；medium=有证据但依赖少量推断；low=缺摘要/缺元数据/有冲突未裁定。
- **同分平局键**（确定性）：`priority_score desc, evidence_completeness desc, year desc, norm_title asc, record_id asc`。

## 4. 去重与冲突规则

- **preprint vs published**：同一研究两个版本 → `dedupe_groups` 中 `canonical` 选正式发表版（有 venue+DOI 且非 preprint），preprint 记为 `version_variant`；无法确定时升级 `pending_human`。
- **同一研究多报道**：标题/DOI 高相似（归一标题 Jaccard≥0.9 或 DOI 相同）聚为一组，记录 `same_study`，`conflict` 说明两方与裁定。
- **标注分歧/多来源不一致**：`conflict` 记录所有来源与逐字片段，`source_conflict` 显式标注；无法裁定 → `pending_human`，不隐藏分歧。
- 判定不可逆：任何裁定都保留原证据，不得用空值覆盖。

## 5. 证据要求（硬要求）

- 每条 `reasons` 至少对应一条 `evidence`，每项含 `locator`（如 `case.json#papers[0].abstract`、`materials/paper.md#p3`）+ 逐字 `quote`（从材料原文复制，不得改写）。
- 关键结论、数值、DOI、年份必须来自材料；无据写 `pending_human` 或 `not_reported`。
- 冲突信息不得隐藏：两方都要记 quote 并标 `source_conflict`。

## 6. 证据完整度（evidence_completeness）

| 值 | 触发条件 |
|-|-|
| `full` | title/abstract/authors/year/venue/doi 齐备 |
| `partial` | 有摘要，但缺部分元数据（year/venue/doi/authors 任一） |
| `abstract_only` | 有摘要但大部分元数据缺失 |
| `missing_abstract` | 仅有标题/作者，无摘要 |
| `none` | 无可用的候选内容 |

规则：`full` 与缺失字段不得同时出现；`missing_abstract`/`none` 时 relevance 必须为 `unknown`、tier 为 `pending`、confidence 为 `low`。

## 7. 不确定性分类（uncertainty，本版核心）

逐条 `uncertainty[i] = {"kind", "note", "affects"}`，`affects` 取 `relevance/tier/score/rank` 的任意子集：

| kind | 含义 |
|-|-|
| `label_missing` | 缺 year/doi/venue/authors 等元数据 |
| `abstract_insufficient` | 摘要信息不足，无法定级 |
| `duplicate_conflict` | 预印本与正式版/多版本未完全裁定 |
| `source_conflict` | 多来源信息不一致 |
| `criteria_conflict` | 纳排标准相互矛盾或与题目冲突 |
| `query_ambiguous` | 研究问题/检索范围表述模糊 |
| `no_fulltext` | 仅凭摘要，无法读全文 |
| `ocr_or_parse_loss` | PDF 解析/OCR 丢失信息 |
| `out_of_scope_inference` | 基于文件名/领域标签的推断，已降置信度 |

顶层 `uncertainty` 汇总全局性问题；`limitations` 记录不可消除的局限（缺数据、缺软件、仅摘要、解析丢失），且必须诚实：**缺数据标注缺失，不编造**。`status=partial` 或 `insufficient_evidence` 时 `limitations` 不得为空。

## 8. 空输入与降级（不编造）

- 空输入/空候选：status=`insufficient_evidence`，写 `omissions` + `limitations`，退出码 0，不生成示例文献。
- 坏 JSON/坏编码：写入 `errors`/`degradations`，用 task.md 继续。
- 缺软件/权限/接口异常：status=`partial`/`input_error`，`errors` 记录，`pending_human` 说明。
- PDF 无法解析：先 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`。
