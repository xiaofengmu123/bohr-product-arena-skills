# 六类结构问题分类法与可执行编辑动作词汇表

本文件是结构诊断的可复用资源：定义六类结构问题、每类的缺陷类型（defect_type）、
每条缺陷必须携带的证据字段，以及作者可直接执行的编辑动作（action_type）。
所有判断必须指回原文 locator + 逐字 quote；模型只核对脚本候选，不凭空生成缺陷。

## 1. 六类结构问题与缺陷类型

每类以稳定键命名；`finding` 结构统一为：

```json
{"finding_id": "F-001", "dimension": "argument_chain",
 "defect_type": "claim_without_evidence", "severity": "high|medium|low",
 "locator": "Sec3:Results ¶2 s1", "quote": "逐字原文",
 "evidence": ["CLM-0003"], "note": "一句话说明为何是结构问题而非风格问题"}
```

| 维度键 | 中文 | 缺陷类型（defect_type） | 考察要点 |
|---|---|---|---|
| `research_question_alignment` | 研究问题与贡献主张对齐 | `research_question_missing`、`contribution_scope_mismatch`、`question_not_echoed_in_conclusion` | 研究问题是否清楚提出并贯穿全文；贡献主张是否与实际完成工作一致；结论是否回答该问题 |
| `section_structure` | 章节结构与叙事顺序 | `information_released_too_early`、`detail_should_move_later`、`sections_should_merge`、`missing_section` | 章节顺序、篇幅分配、信息释放顺序是否服务论证；该前置的铺垫、该后移的细节、该合并的章节 |
| `argument_chain` | 论证链与论据对应 | `claim_without_evidence`、`evidence_strength_insufficient`、`circular_reasoning`、`logical_leap` | 逐条论断是否都有对应论据；是否存在断裂、跳跃、循环论证；论据强度是否足够 |
| `paragraph_relations` | 段落关系与衔接 | `missing_transition`、`abrupt_topic_shift`、`paragraph_should_split`、`paragraph_should_merge` | 段落之间是否缺过渡、逻辑跳跃、话题突变 |
| `redundancy` | 重复内容 | `synonym_redundancy`、`cross_section_duplication` | 同义重复、跨章节重复表述 |
| `conclusion_boundary` | 结论边界与表述尺度 | `overgeneralization`、`correlation_as_causation`、`missing_limitation`、`claim_exceeds_data` | 结论是否超出数据与实验条件范围；是否漏限制说明；是否把相关性写成因果 |

## 2. 诊断四条门（贯穿所有维度）

- **诊得准**：定位到章节/段落/句子，并给出 `defect_type`，不写放之四海而皆准的原则。
- **改得动**：每条 `finding` 必须能转成一条 `edit_action`（见下），不另起一篇改写稿。
- **链得通**：研究问题—方法—结果—结论闭环；每条论断都有论据（`claim_evidence_map`）。
- **说得清**：每条判断指回 `locator` + 逐字 `quote`，作者能判断采纳/部分采纳/驳回。

## 3. 可执行编辑动作词汇表（edit_plan.actions）

动作统一结构：

```json
{"action_id": "EDIT-001", "finding_id": "F-001",
 "dimension": "argument_chain", "action_type": "add_citation",
 "target_locator": "Sec3:Results ¶2 s1",
 "source_locator": "Sec4:Discussion ¶1",   // move/merge 时必填，其余可空
 "before_quote": "逐字原文",
 "instruction": "在该句后补充引用[文献]以支撑论断",
 "rationale": "该论断无对应论据，属于 claim_without_evidence",
 "status": "suggested|confirmed|rejected"}
```

| action_type | 含义 | 必填字段 |
|---|---|---|
| `move` | 把某段/句移动到指定位置之后 | target_locator、source_locator、instruction |
| `merge` | 与指定段落合并（去重后保留一处） | target_locator、source_locator、instruction |
| `split` | 在某句处拆分段落 | target_locator、instruction |
| `narrow` | 收窄论断（补范围/条件/对象/样本量） | target_locator、before_quote、instruction |
| `add_transition` | 在两句/两段间补衔接语 | target_locator、instruction |
| `add_citation` | 为无据论断补引用/图/表/统计量 | target_locator、instruction |
| `delete_duplicate` | 删除重复段落，保留规范出处 | target_locator、source_locator、instruction |
| `add_limitation` | 在结论处补局限/范围限定 | target_locator、instruction |

`instruction` 必须写清"动哪里、怎么动、为什么"，避免"建议优化表达"这类空泛表述。

## 4. 论点—论据映射规则（claim_evidence_map）

每条论断一条 `claim`，映射其论据；缺口显式标注：

```json
{"claim_id": "CLM-0003", "claim": "逐字论断",
 "locator": "Sec3:Results ¶2 s1",
 "evidence_refs": ["Fig.1", "[12]", "表2"],
 "evidence_status": "supported|weak|missing|circular",
 "gap_note": "该论断只有 p<0.05 一个统计量，未给出效应量与置信区间"}
```

- 论据可来自：图/表编号、引用编号、统计量（p/CI/OR/HR/RR/SD/n）、数字+单位、具体实验条件。
- `evidence_status=missing` 时，对应编辑动作用 `add_citation`；`weak` 用 `narrow` 或 `add_citation`；
  `circular` 用 `narrow` 并注明循环论证位置。
- 不新增材料外事实：claim 与 quote 必须来自原文，缺就标 `not_reported`。

## 5. 与语言润色工具的边界

- 本 Skill 只处理**结构级**缺陷；用词、语法、风格、句长、被动语态等属于润色，不产出。
- 判断"风格偏好 vs 结构错误"的标准：删除该内容是否会破坏论证闭环或信息可追溯性。
  会破坏=结构错误；不会、只是"更顺口"=风格偏好，不纳入 diagnosis。
- 不做整段重写：交付的是定位到句子的动作，作者可逐条采纳/驳回；可选给 `suggested_after`
  时必须标注"建议写法，非唯一答案"。

## 6. 严重度（severity）判据

- `high`：断裂/跳跃/循环论证/结论越界，直接影响结论成立。
- `medium`：冗余、缺过渡、缺局限、贡献主张范围漂移，影响可读性与可复核性。
- `low`：细节该后移、铺垫该前置，影响信息释放节奏但不破坏正确性。
