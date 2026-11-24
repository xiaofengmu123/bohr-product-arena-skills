# result.json 输出契约与字段语义

本文定义 `/app/output/result.json` 的字段语义与降级规则。若 `case.json` 的 `output_contract` 声明了额外字段，必须原样回填（缺值 `null`/`[]`），不改名、不删未知键；本文字段是必填基线。

## 顶层必填字段

| 字段 | 类型 | 语义 |
|-|-|-|
| case_id | string | 来自 case.json 的 case_id/id/challenge_id/armiId，或 task.md 的 challenge_id |
| status | string | `ok` 完整可复核；`partial` 已产出但有限制；`insufficient_evidence` 材料不足；`input_error` 输入无法解析 |
| task_type | string | 固定 `experiment_result_interpretation` |
| summary | string | 1–3 句平实结论，含 status 定性 |
| interpretation | object | 五段结论，见下 |
| experiment_plan | object | hypothesis/testable_question/variables/controls/steps/parameters/resources/interpretation_criteria |
| evidence_and_risk | object | evidence_basis/risk_and_anomaly_handling/assumptions |
| confirmation_items | object | safety/ethics/equipment_availability/expert_confirmation |
| sources | array | 实际读取的材料清单，每项建议 {path, kind, locator} |
| uncertainty | array | 单位/方法/阈值歧义、未决问题 |
| change_log | array | {phase, at, note}：骨架→画像→结论→定稿 |
| errors | array | 解析失败等 |
| degradations | array | 坏编码、超量程、单位冲突、未文本化等 |
| limitations | array | 对照缺失、重复数不足、缺软件等限制 |
| pending_human | array | 需专家确认的事项 |
| runtime | object | {started_at, finished_at, seconds} |

## interpretation 五段

每段是数组；每条结论是对象，必须带 `evidence` 数组（每项 {locator, quote, source_conflict?}）。

- trends：方向、区间、对照、拟合方式、幅度与单位。
- anomalies：对象定位、观察值/期望值/偏离、阈值方法与数值、是否超阈值、与对照/重复的偏离。
- possible_causes：候选按证据强度排序；每条 supporting_evidence / excluding_evidence / confound_risk / testability。
- verification_steps：可执行动作；每条 target / expected_if_supported / decision_rule。
- followup_experiments：具体改动；每条 reason / expected_outcome。

## evidence 条目

- locator：文件内定位，如 `materials/paper.md#p3`、`data/batch.csv#L34`、`data.xlsx#Sheet2!C5`。
- quote：材料逐字摘录（不得改写数值/单位/方法名）。
- source_conflict：同一指标两来源不一致时置 true。

## status 与降级

- 空输入 → `insufficient_evidence`，写合法骨架并列缺口，不填充示例。
- 部分材料不可读 → `partial` + degradations；全部关键材料缺失 → `insufficient_evidence`。
- 无法判断 → 写 `not_reported`/`pending_human`，不得编造数据、机理、重复数、DOI、评测分数。

## 确定性

- 脚本只依赖 Python 标准库，输出 JSON 用 sort_keys 稳定排序，无随机数、无时间依赖的数值结论。
- 画像中的离群判定阈值固定：z-score>3、IQR 外 1.5 倍、MAD 修正 z>3.5；重复运行同一输入结果一致。
