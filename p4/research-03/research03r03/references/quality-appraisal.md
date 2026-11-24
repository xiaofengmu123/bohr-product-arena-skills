# 质量评价量表（可复用，须逐条给出依据位置与逐字引文）

本量表为纳入研究提供可解释的评价维度。它不是对单一工具（如 Cochrane RoB 2、
QUADAS-2、ROBINS-I）的复制，而是跨研究设计可复用的最小通用层：每个维度都要给出
`basis_locator`（材料内定位）与逐字 `quote`，否则该维度记 `not_reported`，不得凭印象评级。

## 评级符号

| 符号 | 含义 | 对结论的影响 |
|---|---|---|
| `low` | 低偏倚/低风险 | 结论可作主要支撑，置信可上调 |
| `moderate` | 中等偏倚/部分明确 | 结论可用，但需在 `scope` 说明限制 |
| `high` | 高偏倚/关键信息缺失 | 只能作 `suggestive` 佐证，不得单独支撑强结论 |
| `unclear` | 无法判断 | 记入 `not_reported`，降级该条结论置信 |

## 通用维度（每维度一行）

1. **选择/分配（selection）**：抽样或分组是否清晰，有无明确纳入对象与对照。
2. **暴露/干预定义（exposure/intervention）**：干预、暴露或指标是否可操作定义。
3. **测量（measurement）**：结局或指标是否用可复核方法测量，缺失值如何说明。
4. **混杂/偏倚控制（confounding）**：是否识别并处理主要混杂、盲法、损耗。
5. **报告完整（reporting）**：样本量、效应量、精度（SE/CI）、随访时间是否报告。
6. **外部适用（applicability）**：对象、条件与本研究问题的匹配程度。

## 总体等级规则（确定性，不靠主观“质量较高”）

- 存在任一关键维度 `high` 或 `unclear` 且直接影响结论 → 总体 `high` 风险，结论置信 ≤ `low`。
- 全部关键维度 `low` → 总体 `low` 风险，结论置信可为 `high`（仍需证据数量支撑）。
- 其余 → 总体 `moderate`，结论置信 `moderate` 或 `low`。

## 写入格式（quality.jsonl 一行）

```json
{
  "record_id": "QA01",
  "source_id": "S01",
  "overall": "moderate",
  "dimensions": [
    {"dimension": "randomization", "rating": "low risk",
     "basis_locator": "materials/a.md", "quote": "Patients were randomized 1:1 to intervention or control."}
  ],
  "impact_on_conclusions": "single included trial; downgrade confidence to low"
}
```

规则：`quote` 必须是材料内逐字片段，`selfcheck` 会做字节级子串校验；校验不过的引文记入
`degradations` 并降级，不静默通过。
