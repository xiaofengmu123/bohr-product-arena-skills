# 严重度 / 类型 / 判定 / 建议模板

所有 finding 与 claim 的取值必须来自本文档，避免"一律标中"与"建议不可执行"。

## 1. 类型（type）与严重度（severity）对照

| type | 含义 | 常见严重度 |
|---|---|---|
| factual_error | 可证伪的事实错误 | major/critical |
| overclaim | 结论强度超出数据支撑 | major |
| evidence_gap | 主张缺乏支撑证据 | major/minor |
| citation_mismatch | 引用与所引观点不符 | major |
| method_mismatch | 方法回答不了研究问题 | critical/major |
| statistical_issue | 统计口径/方向/报告问题 | major/minor |
| reasoning_gap | 推理跳跃、隐含假设未说明 | minor/major |
| causal_inference | 相关误作因果 | major |
| inconsistency | 章节前后不一致 | major/minor |
| missing_delivery | 引言承诺未交付 | major |
| norm_issue | 学术规范问题 | minor |
| suggestion | 建议性意见（非缺陷） | suggestion |

## 2. 严重度判据（三问，写入 reasoning）

对每条 finding，用结构化三问回答，severity 由三问共同决定：

```json
"reasoning": {
  "affects_main_conclusion": true,
  "fixable": true,
  "affects_reproducibility": false,
  "note": "一句话说明分级依据"
}
```

| severity | 判定标准 |
|---|---|
| critical | 直接影响主结论，且不修改数据/重做实验无法修正（或影响可复现性导致结论不可信） |
| major | 影响某一核心主张或章节结论，可修正但需要补证据/改结论强度 |
| minor | 局部表述、单句推理或次要口径问题，改一句话即可，不影响主结论 |
| suggestion | 非缺陷，仅优化建议 |

规则：`affects_main_conclusion=true` 且难修正 → critical；影响主结论但可修正 → major；其余 → minor 或 suggestion。**不得一律标 major/minor**，每条都必须在 `note` 里写一句依据。

## 3. 判定状态（status）与置信度（confidence）

| status | 含义 |
|---|---|
| confirmed | 本文内可确证（有逐字原文，推理闭合） |
| probable | 本文内强提示，但需外部信息闭合 |
| pending_review | 无法在本文内闭合，需外部核对/人工判断 |
| ruled_out | 经查证排除（可用于记录已排除的候选问题） |

| confidence | 含义 |
|---|---|
| high | 直接由原文即可确证 |
| medium | 需少量外部信息或推断 |
| low | 依赖较多推断，仅作为提示 |

一致性要求：`status=pending_review` 时 `confidence` 不得为 `high`。

## 4. 建议（suggestion）写作模板

建议必须"补什么证据 / 改哪一句 / 降到什么强度"，禁止空话。逐条可执行，至少包含一个具体动作：

- 降级强度：把 `X causes Y` 改为 `X is associated with Y`（给出新句子）。
- 补证据：在方法第 N 段补报"样本量/缺失处理/混杂调整"。
- 改定位：把讨论第 N 段的因果表述限定为"在本观察性设计中仅支持相关性"。
- 对引用：改为"该研究报告了 …，但未直接检验 …"，或补充一篇直接检验该命题的文献。

每条 suggestion 写清**改哪个定位符、动哪句话、改成什么**。

## 5. 分级与复核的边界

- 无法确认 → `pending_review` + `pending_human` 说明需要人工决策，不凭空补结论。
- 材料缺失/长文截断/章节缺失 → 写入 `limitations`，并如实标 `coverage.<dim>.status=partial`，不得宣称全文已查。
- 证据冲突（同一指标两处不一致）→ 两处都记录，标记 `inconsistency`，不擅自选一个。
