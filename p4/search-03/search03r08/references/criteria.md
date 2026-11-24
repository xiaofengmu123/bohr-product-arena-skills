# 判定口径（criteria）

本文件是逐条判断的判定标准。若 case.json 自带纳排标准或判定口径，**以输入标准为最高优先级**；本文件只在输入未给出足够标准时作为默认口径。所有结论都必须能指回材料的逐字片段（locator + quote）。

## 1. 三级相关性（relevance）

| 级别 | 定义 | 典型情形 | 决策方向 |
|-|-|-|-|
| `direct` | 研究对象、人群、方法、结局与科研问题的核心条件一致，可直接回答研究问题 | 同疾病同人群、同干预/暴露、同结局指标 | retain |
| `indirect` | 部分匹配：对象或人群一致但方法/结局不同，或方法/结局一致但对象不同，只提供背景、方法参考或旁证 | 综述、方法学文献、相邻人群、仅单臂数据 | pending 或 retain（视需要） |
| `irrelevant` | 关键条件不匹配（对象、人群或设计明显不同），无法支持研究问题 | 不同疾病、不同人群、无关主题 | exclude |
| `unknown` | 摘要/元数据不足以判断，且无全文可读 | 缺摘要、只有标题 | pending |

**判定顺序**：先读科研问题抽取核心条件（对象/人群/方法/结局/时间范围），再读候选的标题与摘要逐条比对。标题命中关键词但设计不匹配的，**不得**判为 direct（如标题含"免疫治疗"，摘要却写非目标人群）。

## 2. 三档分层标签（tier）

| 档 | 含义 | 进入条件 |
|-|-|-|
| `retain` | 建议优先复核 | relevance=direct；或 relevance=indirect 且对问题有关键方法/背景价值 |
| `pending` | 信息不足或需人裁定 | relevance=unknown；或 relevance=indirect 且价值不确定；或存在冲突 |
| `exclude` | 排除噪声 | relevance=irrelevant；且理由明确、证据充分 |

**边界约定**：`exclude` 只放 irrelevant；间接相关与未知一律不得进 exclude（保住召回）。`pending` 区尽量小——凡是能给出明确理由的就落到 retain/exclude，只有真正无法裁定才 pending。

## 3. 优先级打分口径（priority_score，0–1）

`priority_score` 由证据支撑的主观判断，**不是**词面先验，也不是脚本自动产出。建议锚点：

| 区间 | 语义 | 说明 |
|-|-|-|
| 0.85–1.00 | 核心证据 | 直接回答核心问题，对象/人群/方法/结局全部匹配，证据完整 |
| 0.65–0.84 | 重要参考 | 直接相关但证据略有缺口，或高价值间接相关 |
| 0.40–0.64 | 一般参考 | 间接相关，或直接相关但年代久/人群部分匹配 |
| 0.10–0.39 | 弱相关 | 仅提供背景，价值低，通常落 pending 或 exclude |
| 0.00–0.09 | 无关 | irrelevant，落 exclude |

打分必须与 `reasons`、`criteria_matched`、`evidence` 一致：同一个分数在两条文献上若理由完全不同，要写清各自依据，不得"看起来合理"。

## 4. 去重与版本冲突规则（dedupe / conflict）

- **预印本 vs 正式发表**：同一研究（规范化标题或相同 DOI 归为同簇）优先保留正式发表版本；预印本标 `dedupe.role=duplicate`，并在 `dedupe.note` 写明与哪个代表合并。
- **同一研究多篇报道**：同簇内取研究对象/结局信息最完整、年份最新者为代表，其余标 duplicate；若两篇报告不同结局或不同随访，标 `role=related_report` 而不合并，避免丢信息。
- **摘要不足**：无法判断是否同簇时，不强行合并，标 `conflict={"kind":"abstract_insufficient"}` 并升级 pending。
- **标注分歧/两来源冲突**：同一指标两来源不一致时，两条证据都记录，`conflict={"kind":"source_conflict","sources":[...]}`，结论标 pending 待人工。
- `conflict` 为 null 表示无冲突；非 null 时必须写清 `kind` 与裁定或升级人工的说明。

## 5. 证据要求（evidence）

- 每条 `reasons` 都应有一条对应的 `evidence`，每项含 `locator`（材料定位，如 `case.json#papers[0].abstract`、`materials/paper.pdf#p3`）+ 逐字 `quote`（从材料原文复制，不得改写、不得臆造）。
- direct/indirect 必须有证据；irrelevant 至少给出排除依据的证据（摘录关键词句即可）；unknown 可无证据，但须在 `uncertainty` 说明缺什么。
- 元数据（title/authors/year/venue/doi）只允许照抄输入；缺失填 `null`，不得猜测或补造 DOI/年份。

## 6. 确定性平局规则

排序固定使用以下平局键（升序为主序）：

```text
tier 序(retain<pending<exclude)
→ priority_score 降序
→ evidence_completeness 降序
→ year 降序
→ norm_title 升序
→ record_id 升序
```

相同输入、相同判断，两次运行必须得到相同排序与标签。`rank` 从 1 开始，并列项按上述键唯一化，不出现同分不同序的随机扰动。

## 7. 完整示例（合成夹具，非正式案例结果）

下面是一条候选文献经逐条判断后的完整落盘形态，用于说明每个字段怎么填、证据怎么溯源。**这是合成示例，不代表任何真实案例或正式答案。**

假设科研问题为「小细胞肺癌（SCLC）脑转移的放疗联合免疫治疗疗效」，候选条目为：

```json
{"id": "p1", "title": "Radiotherapy plus immunotherapy for brain metastases in small cell lung cancer",
 "authors": ["A. Researcher"], "year": 2022, "venue": "J Clin Oncol", "doi": "10.0/example.1",
 "abstract": "Patients with SCLC brain metastases received concurrent radiotherapy and immune checkpoint inhibitor; intracranial objective response was 61%.",
 "source_type": "article"}
```

该条应填写为（示例）：

```json
{
  "record_id": "p1",
  "title": "Radiotherapy plus immunotherapy for brain metastases in small cell lung cancer",
  "authors": ["A. Researcher"], "year": 2022, "venue": "J Clin Oncol",
  "doi": "10.0/example.1", "url": null, "source_type": "article",
  "relevance": "direct",
  "tier": "retain",
  "priority_score": 0.91,
  "rank": 1,
  "reasons": ["研究对象为 SCLC 脑转移患者，干预为放疗联合免疫，结局报告颅内客观缓解，三个核心条件均匹配纳入标准"],
  "evidence": [{"locator": "case.json#papers[0].abstract",
    "quote": "Patients with SCLC brain metastases received concurrent radiotherapy and immune checkpoint inhibitor; intracranial objective response was 61%."}],
  "confidence": "high",
  "criteria_matched": ["研究对象=SClC脑转移", "干预=放疗+免疫", "结局=颅内缓解"],
  "conflict": null,
  "dedupe": null,
  "sort_key": [0, -0.91, -1.0, -2022, "radiotherapy plus immunotherapy for brain metastases in small cell lung cancer", "p1"]
}
```

说明：`reasons` 的每一项都对应一条 `evidence` 的逐字摘录；`criteria_matched` 只列真正命中的纳入/排除条件，命中项必须能在 quote 中找到原文依据；`sort_key` 是 finalize 生成的确定性平局键（tier 序=0 表示 retain，priority_score 取负用于降序），任何人可用它复现排序位置。

若同一条研究存在预印本与正式版两篇，正式版 record 的 `dedupe` 应为 `null`（代表），预印本 record 的 `dedupe` 应写 `{"role":"duplicate","representative_record_id":"p1","note":"与正式发表版合并"}`，且两篇的 `record_id` 都出现在 `dedupe_groups` 的同一簇内。
