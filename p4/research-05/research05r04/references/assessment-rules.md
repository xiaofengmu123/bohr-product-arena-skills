# 评估口径与判定规则（references/assessment-rules.md）

本文件是四项交付的判定口径与证据字段规范，供执行 Agent 与复核者共用。所有判断
必须回到材料中的 `locator` + 逐字 `quote`；无引用信息写 `pending_human` 或
`not_reported`，不补造 DOI、数值或运行结果。

## 0. 证据强度标注（每处结论必填 `strength`）

| 标注 | 含义 | 使用条件 |
|---|---|---|
| `reported` | 材料明示 | 材料中存在逐字事实 |
| `inferred` | 合理推断 | 由多条已报事实推出，且显式声明是推断 |
| `assumed` | 显式假设 | 为推进判断而设的前提，必须写明 |
| `missing` | 材料未报告 | 关键前提在材料中找不到 |
| `conflicting` | 证据冲突 | 两处材料结论相反，需登记到 conflicts |
| `unverifiable` | 无法核验 | 材料不足以判断，不等同于“不存在” |

规则：`missing` 与 `unverifiable` 必须写入 `uncertainty`；“未报告”永远不等于
“不存在”。

## 1. 研究问题定义（research_question）

判定口径：模糊设想必须拆成 研究对象、关键变量、比较基线、预期结论、可证伪条件。

```json
{
  "status": "ok | partial | missing",
  "original": "材料中的原始研究设想（逐字）",
  "testable_statement": "一句话可检验陈述（对象+变量+基线+可证伪条件）",
  "decomposition": {
    "object":          {"value": "...", "evidence": "<evidence_id>", "strength": "reported"},
    "variables":       [{"name": "...", "role": "independent|dependent|confounder",
                         "evidence": "...", "strength": "..."}],
    "baseline":        {"value": "...", "evidence": "...", "strength": "..."},
    "expected_conclusion": {"value": "...", "evidence": "...", "strength": "..."},
    "falsifier":       {"value": "什么观测结果会推翻结论", "evidence": "...", "strength": "..."}
  },
  "gaps": ["材料未给齐的字段及缺口说明"]
}
```

缺字段时该项 `status` 降为 `partial` 或 `missing`，缺口逐条列入 `gaps`，并在
`uncertainty` 登记 `kind=gaps`。

## 2. 检索协议（search_protocol）

判定口径：检索式、纳排规则、筛选结果可独立复核。

```json
{
  "status": "ok | partial | missing",
  "query": ["检索词/检索式（逐条）"],
  "inclusion_criteria": ["纳入标准"],
  "exclusion_criteria": ["排除标准"],
  "screening": [{"source_id": "...", "decision": "include|exclude", "reason": "..."}],
  "coverage": {"scope": "证据覆盖范围", "gaps": ["未覆盖部分"]}
}
```

“检索不到”不写“尚无人研究”，改写“在给定材料/检索范围内未发现”。

## 3. 已有工作比较（prior_work）

判定口径：按 任务/方法/数据/评价方式/结论 五列建比较矩阵，识别真正相近的工作，
标出不可直接比较的原因；禁止跨文档串值。

```json
{
  "status": "ok | partial | missing",
  "comparison_matrix": [{
    "work_id": "...", "source_id": "...",
    "task": "...", "method": "...", "data": "...", "evaluation": "...", "conclusion": "...",
    "comparability": "direct | partial | indirect | none",
    "incomparability_reason": "数据口径/评价指标不同等原因"
  }],
  "closest_work": ["真正相近的 work_id"],
  "notes": ["跨文档不可串值等提醒"]
}
```

## 4. 创新点识别（novelty）

判定口径：区分 方法创新/数据创新/场景迁移/结论修正；场景迁移与结论修正不得误判
为方法创新；每条 novelty 必须有 `covered_by` 与 `verdict`。

```json
{
  "status": "ok | partial | missing",
  "claims": [{
    "claim": "创新陈述（逐字或改写并注明）",
    "type": "method | data | scenario_transfer | conclusion_revision",
    "verdict": "novel | covered | partially_covered | unverifiable",
    "covered_by": ["work_id..."],
    "reason": "判定理由",
    "evidence": "<evidence_id>"
  }],
  "notes": []
}
```

## 5. 可行性判断（feasibility）

判定口径：核查 数据、算力、实验条件、时间窗口、人员能力 等前提；每条前提标
`reported|assumed|missing|conflicting`，`assumed` 必须显式声明；输出带条件结论与
失败风险。

```json
{
  "status": "ok | partial | missing",
  "premises": [{
    "premise": "前提描述", "status": "reported|assumed|missing|conflicting",
    "evidence": "<evidence_id>", "conditional_conclusion": "...", "risk": "..."
  }],
  "conditional_conclusion": "以哪些前提成立为条件的总体结论",
  "failure_risks": ["最可能失败的环节及触发条件"]
}
```

## 6. 证据矩阵 / 冲突 / 综合 / 不确定性 / 限制

### evidence_matrix（每条一行，可回查）

```json
[{
  "evidence_id": "EV-01", "source_id": "materials/paper.txt",
  "locator": "materials/paper.txt#L3-L4",
  "quote": "逐字引文（不缩写、不改写）",
  "field": "novelty | prior_work | feasibility | research_question | search_protocol",
  "claim": "该证据支撑的判断",
  "strength": "reported | inferred | ...",
  "status": "verified | conflicting | unverifiable"
}]
```

### conflicts（相反证据不隐藏）

```json
[{"topic": "...", "side_a": {"source_id": "...", "claim": "..."},
  "side_b": {"source_id": "...", "claim": "..."}, "resolution": "取舍理由"}]
```

### synthesis（综合结论到证据范围为止）

```json
{"conclusions": [{"claim": "...", "strength": "...", "evidence": ["EV-01"]}],
 "research_gaps": ["证据未覆盖的空白"],
 "boundary_statement": "结论只到证据范围为止的一句话边界"}
```

### uncertainty（不确定台账：冲突/偏倚/空白/无法确定）

```json
[{"id": "U-01", "topic": "...",
  "kind": "conflict | bias | gap | unverifiable | inference | ambiguity",
  "claim": "...", "reason": "...", "effect_on_conclusion": "...", "resolution": "..."}]
```

### limitations（本版强化：降级与限制显式化）

与 `uncertainty`（结论层面的不确定）区分：`limitations` 记录**方法与输入层面的
限制**，机器可读，逐条注明影响范围与缓解。

```json
[{"scope": "受限制的对象", "cause": "missing_data | missing_software | truncated |
           encoding_error | no_baseline | no_readable_material | time_budget",
  "severity": "blocking | major | minor",
  "mitigation": "已采取的缓解或需要的输入",
  "affects": ["受影响的结论字段"]}]
```

`status` 取值与 limitations 联动：存在 `blocking` 且关键交付缺失 → `partial`；
无可读材料 → `insufficient_evidence`；输入目录/case.json 损坏 → `input_error`；
缺软件但已降级处理 → `degraded`。
