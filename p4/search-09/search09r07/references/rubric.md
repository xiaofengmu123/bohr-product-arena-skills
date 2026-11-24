# 文献研究质量评价量表（rubric）

本量表把“文献研究质量评价 + 可追溯检索筛选”拆成可判断条目。每条判断都要求一个
`locator`（段落/句子级定位）和一个逐字 `quote`（原文片段）；拿不到证据就判
`insufficient_evidence`，禁止“看起来合理”地补缺。

## 1. 判定值词表（verdict，受限取值）

研究设计 / 证据充分性 / 报告完整性三类条目的 `verdict` 取：

- `satisfied`：原文明确给出支持该检查点的证据。
- `not_satisfied`：原文明确显示该项缺失或不符合。
- `partially_satisfied`：原文只给了部分支持证据。
- `insufficient_evidence`：原文未报告，无法判断（弃权）。
- `not_applicable`：该检查点不适用于本文献类型。

偏倚风险条目的 `verdict` 取：

- `present`：原文存在该偏倚的明确迹象。
- `possible`：设计/报告方式提示可能存在，但无直接证据。
- `absent`：原文明确采取了消除该偏倚的措施。
- `insufficient_evidence`：原文信息不足，无法判断。

任何不在上述词表中的 `verdict` 都会被 `validate` 报 error，强制评价者使用受限取值，
保证双人复核可比。

## 2. 四类维度与通用检查点

### 2.1 研究设计（study_design）

| 检查点 | 需要什么证据 | 何时判不可评价 |
|-|-|-|
| design_type | 明确指出设计类型（RCT / 队列 / 病例对照 / 横断面 / 定性 / 系统综述等） | 无法从原文确定设计类型 |
| comparison_group | 对照/比较组的定义与来源 | 无对照描述 |
| allocation_randomization | 分配/抽样方法与随机化（适用 RCT） | 未报告分配方式 |
| blinding | 盲法层级（受试者/干预者/评估者） | 未报告 |
| sample_size_rationale | 样本量依据或把握度说明 | 无样本量说明 |
| inclusion_exclusion | 纳入/排除标准 | 无标准 |

### 2.2 证据充分性（evidence_sufficiency）

| 检查点 | 需要什么证据 | 何时判不可评价 |
|-|-|-|
| outcome_definition | 主要结局的定义与测量方式 | 结局未定义 |
| followup_completeness | 随访率/失访及处理（适用队列/RCT） | 未报告随访 |
| statistical_methods | 统计方法与假设检验口径 | 无统计方法说明 |
| effect_size_ci | 效应量及置信区间/精度 | 只给 p 值或无精度 |
| sensitivity_analysis | 敏感性/亚组/稳健性分析 | 未报告 |

### 2.3 偏倚风险（bias_risk）

| 检查点 | 适用文献类型 | 明确存在 vs 可能存在的判据 |
|-|-|-|
| selection_bias | 几乎所有设计 | 原文说明非随机/方便抽样 → present；仅未说明 → insufficient_evidence |
| performance_bias | 干预性研究 | 明确未设盲 → present；盲法层级不全 → possible |
| detection_bias | 结局评估相关 | 评估者知道分组且结局主观 → present |
| attrition_bias | 纵向/随访研究 | 失访率显著且未做处理 → present |
| reporting_bias | 所有 | 只报显著结果 / 结局与注册不一致 → present |
| confounding | 观察性研究 | 未校正主要混杂 → present；只校正部分 → possible |

### 2.4 报告完整性（reporting_completeness）

| 检查点 | 需要什么证据 |
|-|-|
| registration | 注册号/伦理审批（适用试验） |
| data_availability | 数据/代码可得性声明 |
| funding_conflict | 基金与利益冲突声明 |
| limitation | 局限性讨论 |
| reference_completeness | 引用完整性（综述类） |

## 2.5 文献类型特有检查点

按识别出的 `design_type` 追加下列检查点（`critical` 默认置 True）：

| 文献类型 | 特有检查点 |
|-|-|
| RCT | allocation_concealment、ITT_or_per_protocol、trial_registration |
| 队列研究 | exposure_definition、confounding_control、loss_to_followup |
| 病例对照 | case_definition、matching_strategy、recall_bias |
| 横断面 | sampling_frame、response_rate、temporality_note |
| 系统综述 | search_strategy、study_selection、risk_of_bias_tool、prisma_compliance |
| 定性研究 | reflexivity、saturation、triangulation |

## 3. 证据优先级顺序与记录

对同一检查点，按以下优先级采证，并把所用来源写入 `basis`：

1. 方法 / 结果章节原文（首选）。
2. 表格 / 图注中的数值与表述。
3. 摘要中的明确陈述（仅当全文不可得时，且须标注 `abstract_only`）。
4. 全文不可得、仅摘要时：可评价条目降级，其余判 `insufficient_evidence`。

图表不可解析时：不凭图推断数值，在 `degradations` 记录“图表未解析”，相应条目判
`insufficient_evidence`。

`basis` 建议格式（可机读）：`method_section | table_2 | abstract_only | source_conflict`。
同一指标多来源不一致时，逐条记录且标 `source_conflict`，并在 `uncertainty` 写分歧点。

## 4. 分级阈值（aggregation）

默认阈值（`scripts/core_impl.py` 的 `DEFAULT_GRADE_RULES`）：

- `high`：任一 `critical=True` 条目的 verdict ∈ {`not_satisfied`, `present`}。
- `moderate`：非 critical 的 `not_satisfied`/`present` 数 ≥1，或 `possible`/`partially_satisfied`
  数 ≥2，或 `partially_satisfied` 数 ≥3。
- `unclear`：可评价条目数为 0，或 `insufficient_evidence` 占可评价条目比例 ≥0.5。
- `low`：无失败信号且证据充分。

`critical` 标记由评价者按检查点是否直接决定结论可信度设定（默认：分配/随机化、盲法、
主要结局、各偏倚领域为 critical）。阈值可被显式覆盖，但必须在 `grade` 输出的
`thresholds` 中记录实际使用的阈值与理由。

### 4.1 分级算例（示例，非正式案例）

给定 4 条判定：`{critical=True, verdict=not_satisfied}`、`{critical=False, verdict=satisfied}`、
`{critical=False, verdict=possible}`、`{critical=False, verdict=insufficient_evidence}`。
聚合：critical_fail=1 → `grade=high`，理由为“存在关键检查点失败，结论可信度受直接威胁”。
该等级必须随 `grade` 输出的 `signals`/`thresholds`/`basis` 一起写入结果，保证可复核。

## 5. 弃权规则

- 原文未报告 → `insufficient_evidence`，`judgment` 写“证据不足，无法判断”，不写推断性结论。
- 一条判断若只能靠摘要、且摘要未覆盖 → `insufficient_evidence` + `limitations` 标注。
- 冲突信息不隐藏：同一指标多来源不一致时，逐条记录并标 `source_conflict`。
- 缺证据不写“合理推测”；若存在值得人工确认的点，写进 `pending_human` 而非 `assessment`。

## 6. 检索与筛选（traceability）

检索流程保留可复核记录：

- `search`：范围 / 查询式 / 时间窗 / 语言 / 检索过的来源（`sources_queried`）。
- `candidates`：每篇带 `id/title/authors/year/venue/doi/source_uri/relevance/
  screening_decision/rationale/locator/quote`。
- `screening`：每步的纳入/排除判据、决策与理由（如标题筛、摘要筛、全文筛）。
- `omissions`：未检索到的库、权限受限、无全文、语言受限、遗漏说明。

空结果、冲突、缺失元数据、权限问题如实写入 `omissions`/`uncertainty`/`pending_human`，
不补造文献；候选集为空时 `status=insufficient_evidence` 并说明已完成与未完成部分。

### 6.1 可复用模板（示例，仅演示结构）

```json
{
  "candidates": [
    {"id": "C1", "title": "…", "authors": "…", "year": null, "venue": "…",
     "doi": null, "source_uri": "…", "relevance": "high",
     "screening_decision": "include",
     "rationale": "与研究问题直接相关",
     "locator": "src#p1", "quote": "…"}
  ],
  "assessment": [
    {"dimension": "study_design", "checkpoint": "blinding",
     "critical": true, "verdict": "not_satisfied",
     "judgment": "未报告盲法", "locator": "src#p3",
     "quote": "Blinding was not reported.", "basis": "method_section"}
  ]
}
```

模板为示例，正式运行时用真实输入材料与原论文填写，不预写正式答案。

## 7. 边界与价值

本量表聚焦“可复核的质量分级 + 可追溯检索筛选”：用受限判定值保证双人可比，用
`locator + quote` 保证可回溯，用分级阈值保证等级可解释，用弃权规则避免补造。相对
通用摘要改写：它把评价从“整体印象”变为“逐条证据”。相对 CONSORT/PRISMA 等公开报告
清单与期刊编辑初筛：它不追求覆盖清单全部条目，也不替代人工裁决，而是把人工改动沉淀
为量表/阈值改进的入口。
