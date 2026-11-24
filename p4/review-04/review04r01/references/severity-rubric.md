# 严重度分级、类型判定与证据映射规范

## 1. 严重度分级（必须可解释，禁止一律“中”）

| 严重度 | 判定标准 | 典型情形 |
|---|---|---|
| critical | 直接动摇主结论，或使主要证据不可信 | 主结论方向被数据反驳；核心统计方向相反；方法完全无法回答研究问题 |
| major | 影响主结论的强度或泛化范围，但可通过补充证据/降级结论修正 | 结论超出数据范围；关键承诺未交付；引用不支持核心论点 |
| minor | 不影响主结论，但影响某条具体表述或可复现细节 | 单处数值前后不一致；样本量/口径描述含糊 |
| suggestion | 非缺陷，仅是可改进的建议性意见 | 建议补充效应量、建议补充数据可得性声明 |

**每条 finding 的 `reasoning` 必须写三问**：①是否影响主结论？②是否可修正（补什么/改哪句）？③是否影响可复现性？据此定严重度，而非按感觉。

## 2. status 与 confidence

- `status`：`confirmed`（两处原文相互印证或可外部核验成立）、`probable`（单处证据、推断成立）、`pending_review`（需外部核对）、`ruled_out`（核查后排除，保留以说明核查轨迹）。
- `confidence`：`high / medium / low`。证据充分才给 `high`；推断性判断给 `medium`；依赖未获全文的引用给 `low`。

## 3. 类型与证据要求（节选，与 checklist 对应）

- `factual_error`：给出两处 locator + 两处逐字 quote，说明冲突；
- `overclaim`：给出结论句 quote + 数据/方法证据 quote，说明强度差；
- `unsupported_citation`：给出所引观点句 quote，标 `pending_review` 若无法核对原文；
- `method_mismatch`：给出研究问题句 + 方法句，各带 quote；
- `statistical_error`：给出统计描述句 + 结论描述句；
- `argument_gap`：给出前提句 + 结论句，指出跳跃处；
- `causality_conflation`：给出因果表述句 + 设计描述句；
- `cross_section_gap`：给出承诺句 + 交付位置（或 `null`）；
- `consistency_issue`：给出两处不一致的 quote；
- `norm_violation` / `suggestion`：给出定位，suggestion 必须标注“建议，非缺陷”。

## 4. 可执行修改建议（suggestion 的写法）

必须落到具体句子，说清三选一或多选：

1. **补证据**：补哪条数据/实验/引用；
2. **改句子**：把哪句改成什么强度（例：把“证明”改为“提示”、把“所有条件稳健”改为“已报告的两种条件下稳健”）；
3. **降级结论**：把哪个结论从因果/普适降为相关/特定范围。

禁止“建议补充论证”“建议加强逻辑”这类仍需人工重写的空话。

## 5. 证据映射 schema（本 Skill 强化点）

`review.evidence_map`：

```json
{
  "items": [
    {
      "id": "E001",
      "source": "/input/materials/paper.pdf",
      "locator": "p3 ¶P0012",
      "quote": "逐字原文",
      "char_span": [1234, 1456],
      "kind": "direct_quote",
      "status": "verified",
      "supports": ["F001", "C001"],
      "contradicts": [],
      "note": ""
    }
  ],
  "by_finding": {"F001": ["E001"]},
  "by_claim": {"C001": ["E001"]},
  "conflicts": [
    {
      "id": "X001",
      "metric": "误差降低幅度",
      "source_a": {"source": "...", "locator": "¶P0001", "quote": "90%"},
      "source_b": {"source": "...", "locator": "¶P0004", "quote": "9%"},
      "resolution": "待人工确认"
    }
  ]
}
```

- `by_finding` / `by_claim` 由脚本在每次 `update` 后**自动重建**，Agent 只需写 `items` 与 `supports/contradicts`；
- `kind=missing_evidence` 时 `quote` 可为空，`locator` 指向“应当出现但缺失”的位置；
- `char_span` 为段内近似字符偏移，辅助快速检索，`locator` 才是权威定位。

## 6. 正误示例

**反例（不可交付）**：

```json
{"id":"F001","type":"overclaim","severity":"medium","locator":"结果章节","quote":"","claim":"结论过强","evidence":"感觉数据不足","reasoning":"","suggestion":"建议加强逻辑"}
```

**正例（可复核、可执行）**：

```json
{
  "id": "F001", "type": "overclaim", "severity": "major",
  "locator": "p4 ¶P0012",
  "quote": "因此该算法在所有噪声条件下均稳健。",
  "claim": "算法在所有噪声条件下稳健",
  "evidence": "结果章节仅报告两种噪声条件（¶P0009），第三种条件（¶P0006 引言承诺）未报告。",
  "reasoning": "影响主结论：是（普适性结论）。可修正：补充第三种条件结果，或将结论降级为“已报告的两种条件下稳健”。影响可复现：是（缺第三种条件数据）。",
  "suggestion": "将讨论句改为“在已报告的两种噪声条件下稳健”，并在结果章节补充第三种条件的误差与显著性检验。",
  "status": "confirmed", "confidence": "high",
  "evidence_refs": ["E001", "E002"]
}
```
