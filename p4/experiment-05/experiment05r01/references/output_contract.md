# result.json / evidence_map.json 字段契约

本文件是 SKILL.md 输出契约的精确定义。运行时若 `/input/case.json` 含 `output_contract`，必须原样回填其全部键（缺值写 `null`/`[]`，不改名、不删未知键）；若无机器可读契约，则用下方兜底结构。

## 1. result.json 顶层字段

| 字段 | 类型 | 要求 |
|---|---|---|
| `case_id` | string | 必填非空；来自 case.json 的 `case_id`/`id`/`challenge_id`/`armiId` 或 task.md |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error`，必须诚实 |
| `task_type` | string | 固定 `experiment_result_interpretation` |
| `summary` | string\|null | 1–3 句平实结论 |
| `interpretation` | object | 五段结论，见第 2 节 |
| `experiment_plan` | object | 实验方案，见第 3 节 |
| `evidence_and_risk` | object | 依据与风险，见第 4 节 |
| `confirmation_items` | object | 确认项，见第 5 节 |
| `evidence_map` | object\|null | 指向 evidence_map.json 的摘要（file/generated_at/coverage） |
| `sources` | array | 实际读取过的来源（file/kind/encoding/size_bytes/sha256_head） |
| `uncertainty` | array | 单位/方法/阈值歧义、未统一换算、冲突点 |
| `change_log` | array | `{at, stage, note}`，记录 骨架→画像→结论→证据映射→定稿 |
| `errors` | array | 坏 JSON、不可读文件等错误 |
| `degradations` | array | 坏编码降级、文件超限跳过等 |
| `limitations` | array | 对照缺失、重复数不足、超量程、材料缺口 |
| `pending_human` | array | 未经专家确认、不可下结论的事项 |
| `runtime` | object | `{start, end, seconds}` |

## 2. interpretation（五段结论）

`trends` / `anomalies` / `possible_causes` / `verification_steps` / `followup_experiments` 五键，均为数组；每个条目是对象：

```json
{
  "id": "anomaly-03",
  "statement": "……结论……",
  "evidence": [ {evidence 条目, 见第 6 节} ],
  "supporting_evidence": [],
  "excluding_evidence": [],
  "confound_risk": "相关≠因果的风险说明",
  "testability": "能否用现有数据或一个实验判定"
}
```

条目类型要求：

- **trends**：写方向（增/减/稳定/非单调/拐点）、区间、对照、拟合方式、幅度与单位。禁止“读数复述”。
- **anomalies**：定位到样本/批次/通道/孔位/时间点；给观察值、期望值、偏离幅度、判定阈值（写明 z-score/IQR/MAD/Grubbs/手动及数值）、是否超阈值、与对照/重复的偏离关系。
- **possible_causes**：按证据强度排序；每条带 `supporting_evidence` 与 `excluding_evidence`、`confound_risk`、`testability`；无排除过程的机制猜测标低置信并写明缺哪步验证。
- **verification_steps**：可执行动作，每条写 `target`、`expected_if_supported`、`decision_rule`；禁止“检查数据质量”这类空话。
- **followup_experiments**：对应原因候选与证据缺口写具体改动，并写 `reason` 与 `expected_outcome`。

## 3. experiment_plan

`hypothesis` / `testable_question` / `variables` / `controls` / `steps` / `parameters` / `resources` / `interpretation_criteria` 八键。完成标准：实验人员可据此执行，或明确知道缺什么。

## 4. evidence_and_risk

`evidence_basis`（文献/设备/历史记录依据）/ `risk_and_anomaly_handling`（风险与异常处置）/ `assumptions`（假设）。完成标准：假设和边界可复核。

## 5. confirmation_items

`safety` / `ethics` / `equipment_availability` / `expert_confirmation` 四键。未经确认的建议不得写成可执行事实，进 `pending_human`。

## 6. evidence 条目（每条关键结论都必须有）

```json
{
  "evidence_id": "E012",
  "locator": "materials/cefixime_paper.pdf#p5  或  data/batch_12.csv#force_N:L34",
  "quote": "材料中的逐字摘录（≤400 字符）",
  "metric": "force_N / accuracy / assay_percent",
  "value": 12.34,
  "unit": "N",
  "method": "column_peak / table_cell / paper_text",
  "threshold": null,
  "source_conflict": false,
  "confidence": "high | medium | low"
}
```

- `locator` 统一格式：`文件相对路径#定位片段`；CSV 可带列名与行号，PDF 可带页码。
- 同一指标两个来源不一致时两条都记录并标 `source_conflict`（可为 true 或冲突说明字符串）。

## 7. evidence_map.json（细粒度证据映射）

`python scripts/main.py evidence` 从 result.json 提取生成，结构：

```json
{
  "schema_version": "1.0",
  "case_id": "…",
  "generated_at": "…",
  "claims": [ {"claim_id":"trend-01","type":"trends","statement":"…"} ],
  "evidence": [ 规范化后的 evidence 条目，含 located_at 路径 ],
  "claim_to_evidence": { "trend-01": ["E001"] },
  "conflicts": [ {"evidence_id":"E003","locator":"…","note":"…"} ],
  "coverage": { "total_claims":n,"claims_with_evidence":n,
                "claims_without_evidence":[…],"total_evidence":n,"total_conflicts":n }
}
```

`claims` 的 id 来自五段条目与 `experiment_plan.hypothesis`/`testable_question`；`claim_to_evidence` 把每条 claim 回指到其 evidence id；`coverage` 用于自检“每条结论是否都有证据”。

## 8. 三种真实输入形态的路由

| 形态 | 典型材料 | 分析要点 |
|---|---|---|
| 197 条弯曲曲线 | 多列时序 CSV/TSV + 样本工作簿/MATLAB | 逐 trace 提取峰值力、线弹性段斜率（刚度）、拐点；跨 trace 用 z-score/IQR/MAD 定位离群 trace，并绑定 trace id/批次/通道 |
| Iris 5 折 / 2 估计器计算实验 | 折指标 CSV/JSON + 实验说明 | 逐折提取 accuracy 等指标，算均值±std；比较两个估计器；用 z-score 定位异常折 |
| Cefixime 12-sheet 工作簿 + 论文 | XLSX + PDF 论文 | 先用 pandas/openpyxl 导出各 sheet 为 CSV，再画像；从论文提取验收阈值/标准（如 ICH 接受限），逐 sheet 比对数据与阈值 |

不依据错误的领域标签补造数据；材料形态未知时先 `inspect_data.py` 画像，再按列名与文件内容判断。
