# 字段语义、证据规范与 fallback 结构

本文件是 SKILL.md 第 3/5 节的细则，供 Agent 在回填 `result.json` 时对照。**凡与 case.json 的 `output_contract` 冲突，以 `output_contract` 为准**；此处仅为缺契约时的 fallback 与语义解释。

## 1. fallback 输出结构（无 output_contract 时）

```json
{
  "plan": {
    "hypotheses_and_variables": {},
    "controls": [],
    "equipment_materials": [],
    "time_cost": {},
    "parameter_ranges": [],
    "risks_and_abnormalities": []
  },
  "evidence_and_risks": {"evidence_map": [], "risks": []},
  "confirmations": []
}
```

再加信封字段：`case_id`、`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`contract_coverage`。

## 2. 六要素语义（落到操作定义/单位/量程/判据）

- **hypotheses_and_variables（假设与变量）**：目标转成可检验假设（H0/H1 或可判读命题）；每个变量写操作定义、测量方式、单位、取值范围；明确自变量、因变量、混杂因素及控制方式。
- **controls（对照组）**：空白/阳性/阴性/剂量梯度等；每条说明**排除哪类混杂**，避免只有一组空白对照或对照与目标不匹配。
- **equipment_materials（设备材料）**：步骤绑定具体设备型号、量程、耗材规格、试剂；标出可替代项与不可替代项。
- **time_cost（时间成本）**：步骤时长、并行关系、样本量、预算估算；能据此排期与报价；预算有明细与合计。
- **parameter_ranges（参数范围）**：关键参数、单位、允许区间、超区间处置；依据标注来源（设备手册 / 预实验 / 文献 / 案例给定约束）。
- **risks_and_abnormalities（风险与异常）**：失败模式（污染、无信号、重复性差、样本不足等）、判据、止损点、替代路径。

每项至少做到"实验人员可据此执行，或明确知道缺什么"。

## 3. 证据溯源规范

- 证据项结构：`{"locator": "…", "quote": "…", "claim": "…", "source_conflict": false}`。
- `locator` 示例：`materials/paper.md#p3`、`materials/supplier_contract.md#L12`、`pdftotext/paper.txt` 或 PDF 页码。
- `quote` 必须是材料中的**逐字原句**，不得改写；关键数值必须出现在 quote 中。
- 冲突：同一指标两处来源不一致时，两条都记，并设 `source_conflict: true`，在 `uncertainty` 中说明冲突双方与影响。
- 缺失：数值/型号/价格/批准未知 → 写 `not_reported` 或列 `pending_human`，绝不编造 DOI/价格/排期。

## 4. 信封字段语义

- `status`：`ok`（完整可用）/ `partial`（可执行但有缺口）/ `insufficient_evidence`（材料不足，无法形成可执行方案）/ `input_error`（输入不可解析）。
- `sources`：`[{"path": "…", "type": "case|task|pdf|contract|candidate_plan"}]`。
- `uncertainty`：`[{"item": "…", "reason": "…", "impact": "…", "alternatives": ["…", "…"]}]`。
- `pending_human`：`[{"item": "…", "why": "…", "who": "safety|ethics|equipment|expert"}]`。
- `change_log`：`[{"ts": "ISO8601Z", "event": "…", "detail": "…"}]`；每次大改动追加一条。
- `contract_coverage`：`{"<键路径>": "filled|empty"}`，由 scaffold/validate 计算，作为契约逐键对齐的核验记录。

## 5. 算术校验约定

- `arithmetic_checks`（可选，落在 plan 内）每项 `{"label", "expression", "expected"}`；`expression` 仅允许数字与 `+ - * / ( )`，`validate` 会安全求值并比对 `expected`。
- 预算/排期/参数裁剪的加减乘除、样本量×单价、时间窗 vs 样本量×单样本时长等，都应写成 `arithmetic_checks` 以便机器复核；不能机器核验的主观判断写进 `uncertainty` 或 `pending_human`。

## 6. 与 plan.md 的一致性

`plan.md` 是 result.json 的人类可读视图：六要素、依据与风险、确认项必须逐项对应；数值与单位一致；未确认项在 plan.md 中同样标注"待确认"而非事实。
