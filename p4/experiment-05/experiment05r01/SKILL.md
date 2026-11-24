---
name: experiment-evidence-interpreter
description: 科研实验结果解释与异常分析 Skill：把实验读数、曲线、图谱与设备日志转成趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论，并为每条结论绑定 locator+逐字 quote，生成细粒度 evidence_map；支持异常定位、原因排序与可执行复核清单。适用于有真实数据文件（CSV/TSV/JSON/曲线/XLSX/PDF 论文）且需回答“趋势、哪个点异常、为何、复核什么、补做什么实验”的场景；不适用于无数据纯理论问答、需合规签字的临床与安全结论。
version: 1.1.0
---

# 实验结果解释与异常分析（证据溯源强化版）

## 1. 角色与何时使用

本 Skill 面向“一轮实验跑完后”的解释工作：把原始读数、时序/弯曲曲线、多批次多通道数据、图谱与设备日志，转化为五段可检查结论，并让每条结论可回指到材料中的逐字片段。当输入目录含数据文件且任务要求解释趋势、判定异常、排序原因、规划复核/补做实验时使用。

不适用：无任何数据文件的纯理论问答、需合规签字的临床与安全结论、从零设计全新实验方案（本 Skill 只把方案作为解释依据补全）。

本版侧重**证据溯源强化**：每条结论都带 `evidence_id` + `locator` + 逐字 `quote`，`evidence_map` 增加 `claims → evidence` 归属、冲突与覆盖统计三层结构，而非只把 evidence 塞进每条结论。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（案例元数据与约束，可能含 `case_id`/`id`/`challenge_id`/`output_contract`）、`/input/task.md`。
- 材料文件在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/MAT 等）。**必须实际读取文件内容**，不得按文件名或领域标签臆断来源；PDF 解析拿到的是原文，没有预抽取答案层。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并记 `degradations`；坏 JSON 记 `errors` 后继续处理；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（临时），最终产物写 `/app/output/`。

三种真实输入形态的识别与处理：

1. **197 条弯曲曲线**（多列时序 CSV/TSV + 样本工作簿/MATLAB）：逐 trace 提取峰值力、线弹性段斜率（刚度）、拐点；跨 trace 用 z-score/IQR/MAD 定位离群 trace，并绑定 trace id/批次/通道。
2. **Iris 5 折 / 2 估计器计算实验**（折指标 CSV/JSON + 实验说明）：逐折提取 accuracy 等指标，算均值±std；比较两估计器；用 z-score 定位异常折。
3. **Cefixime 12-sheet 工作簿 + 论文**（XLSX + PDF）：先用平台 pandas/openpyxl 把各 sheet 导出 CSV，再画像；从论文提取验收阈值，逐 sheet 比对数据与阈值。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`data_profile.json`。result.json 顶层字段：

`case_id`（必填）、`status`（`ok | partial | insufficient_evidence | input_error`）、`task_type`、`summary`、`interpretation`、`experiment_plan`、`evidence_and_risk`、`confirmation_items`、`evidence_map`、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`。

- 若 `case.json` 含 `output_contract`，**原样回填其全部键**（缺值 `null`/`[]`，不改名不删键）；否则用 `references/output_contract.md` 的兜底结构。
- `interpretation`：`trends` / `anomalies` / `possible_causes` / `verification_steps` / `followup_experiments`。
- `experiment_plan`：`hypothesis` / `testable_question` / `variables` / `controls` / `steps` / `parameters` / `resources` / `interpretation_criteria`。
- `evidence_and_risk`：`evidence_basis` / `risk_and_anomaly_handling` / `assumptions`。
- `confirmation_items`：`safety` / `ethics` / `equipment_availability` / `expert_confirmation`。
- 每条关键结论必须带 `evidence` 数组，每项含 `evidence_id`、`locator`（如 `data/batch_12.csv#force_N:L34`）、`quote`（逐字摘录）；字段精确定义见 `references/output_contract.md`。
- `status` 必须诚实：材料不足写 `insufficient_evidence`，绝不编造数据、机理或重复数。

## 4. 执行流程（总 900 秒、64 次模型请求、单次 16384 token）

时间预算按 15 分钟 / ≤64 次请求规划；**前 3 分钟内必须先落盘一版合法 result.json 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。64 次请求是上限，不是目标。

- **P0 骨架落盘（0–3 分钟）**：读 case.json + task.md，解析 case_id，运行 `python scripts/main.py skeleton --input /input --output /app/output`；立即写出含全部顶层字段的 result.json（status=`partial`）与 report.md 骨架。输入为空则 status=`insufficient_evidence` 并列出缺口。
- **P1 数据清点与画像（3–8 分钟）**：运行 `python scripts/inspect_data.py --input /input --output /app/output` 生成 `data_profile.json`（文件清单、每列 n/缺失/均值/中位数/标准差/分位数、z-score/IQR/MAD 离群标记、单位提示、饱和检测）。定位每个数值到具体文件/列/行。XLSX/MAT 先用平台 pandas/openpyxl 导出 CSV 后再画像。
- **P2 五段结论与证据映射（8–13 分钟）**：依据画像与原文逐条写 `interpretation`（见第 5 节），补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 `evidence_id`+locator+quote；每写一段就增量写回 result.json。
- **P3 证据映射、自检与定稿（13–15 分钟）**：运行 `python scripts/main.py evidence` 生成细粒度 `evidence_map.json`；运行 `python scripts/main.py report` 生成最终 report.md；运行 `python scripts/main.py validate` 校验 schema 与证据完整性；补 `change_log`、`uncertainty`、`limitations`、`pending_human`；最后 `python scripts/main.py finalize` 写 runtime。

每阶段结束都增量落盘；模型请求优先用于“读原文→提取事实→核对证据”，不用于无依据扩展。

## 5. 五段结论规范

- **trends 趋势**：写方向（增/减/稳定/非单调/拐点）、依据区间、对照、拟合方式（线性/滚动均值/目测）、幅度与单位。禁止“读数复述”。
- **anomalies 异常点**：定位到样本/批次/通道/孔位/时间点；给观察值、期望值、偏离幅度、判定阈值（写明 z-score/IQR/MAD/Grubbs/手动及数值）、是否超阈值、与对照/重复的偏离关系。
- **possible_causes 可能原因**：按证据强度/可检验性排序；每条写 `supporting_evidence`、`excluding_evidence`、`confound_risk`、`testability`；无排除过程的机制猜测只能标低置信并写明缺哪步验证。
- **verification_steps 复核步骤**：写成第二人可执行的核查动作——回查具体文件/字段/行或 sheet、核对批次与设备日志、加做对照或平行重复；每条写 `target`、`expected_if_supported`、`decision_rule`。禁止“检查数据质量”。
- **followup_experiments 后续实验建议**：对应原因候选与证据缺口写具体改动（补对照、加密采样、调参数范围、扩重复数），并写 `reason` 与 `expected_outcome`。

## 6. 证据溯源与细粒度 evidence_map（本版重点）

- 每条关键结论/数值/图元都要能指回材料逐字片段：`locator`（文件#定位）+ `quote`（逐字摘录）+ `evidence_id`。
- 同一指标两个来源冲突时两条都记录并标 `source_conflict`；不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`；单位/方法/阈值有歧义时保留歧义，不擅自统一。
- `evidence_map.json` 由 `main.py evidence` 从 result.json 确定性地提取生成，含 `schema_version`、`case_id`、`claims`（声明列表）、`evidence`（规范化证据，含 `located_at` 路径）、`claim_to_evidence`（claim→evidence 归属）、`conflicts`（冲突列表）、`coverage`（覆盖率：有/无证据的 claim、证据总数、冲突总数）。
- `coverage.claims_without_evidence` 用于自检：最终 status=`ok` 前应清空，无法补齐则如实降级并写入 `limitations`。

## 7. 脚本调用（相对路径，只用标准库）

在 Skill 根目录下执行：

```bash
python scripts/main.py skeleton --input /input --output /app/output
python scripts/inspect_data.py --input /input --output /app/output
python scripts/main.py evidence --input /app/output/result.json --output /app/output
python scripts/main.py report --input /app/output/result.json --output /app/output
python scripts/main.py validate --input /app/output/result.json
python scripts/main.py finalize --input /app/output/result.json
python scripts/selftest.py
```

`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。脚本仅用 Python 标准库；失败时退出码非 0 并在 stderr 说明，Agent 不得因脚本失败而编造结果。

## 8. 自检清单（提交/定稿前逐条勾选）

- [ ] result.json 可被 `json.load` 解析，`case_id` 非空，`status` 合法
- [ ] 五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（evidence_id + locator + quote），可回指具体文件/字段/行
- [ ] 异常点写明阈值方法+数值；原因写明支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（含 target/判据），无空话
- [ ] 单位/方法/阈值歧义被保留并写入 `uncertainty`
- [ ] 缺失材料、坏编码、超量程、对照缺失、重复数不足写入 `limitations`/`degradations`
- [ ] `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填；未确认事项进 `pending_human`
- [ ] `evidence_map.coverage` 显示有证据的 claim 全部覆盖；`change_log` 记录各阶段；`runtime` 含耗时
- [ ] 重复运行同一输入结果一致（确定性排序、无随机数依赖）

详见 `references/checklist.md`。

## 9. 降级策略

- 无数据/空输入 → status=`insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON/坏编码 → 记 `errors`/`degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，在 `uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`。
- 缺软件/缺依赖 → 如实标注 `limitations`；本 Skill 脚本只依赖标准库，XLSX/MAT/PDF 需平台 pandas/openpyxl 或 PDF 工具导出，缺失则说明并用标准库兜底。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断写 `not_reported`/`pending_human`。

## 10. 边界

一个 Skill 只解决“实验结果解释与异常分析”这一类任务，不扩展为通用分析平台。没有真实材料时输出“材料不足”而非示例填充。交付的 result.json 是运行时产物，Skill 包内不预置正式案例答案或白板。
