---
name: experiment-interpretation-calibrated
description: 科研实验结果解释与异常分析 Skill：把读数、曲线、图谱与设备日志转成可复算的五段结论（趋势、异常点、可能原因、复核步骤、后续实验建议），输出结构化 result.json 与可编辑 report.md。每个结论强制带证据定位（locator+逐字 quote）、证据分级（G0-G3）与置信依据；内置"禁编造门控"，材料不足就写 insufficient_evidence/pending_human，绝不补造湿实验读数、重复数、原因或文献。当需要解释实验数据、定位异常、排序原因或规划复核/补做实验时使用。
version: 2.0.0
---

# 实验结果解释与异常分析（校准版）

## 1. 角色与何时使用

面向"一轮实验跑完后"的解释工作，把原始读数、时序/弯曲曲线、多批次多通道数据、图谱与设备日志，转化为**趋势、异常点、可能原因、复核步骤、后续实验建议**五段可检查结论，并给出**实验方案（假设/变量/对照/步骤/参数/资源/判读标准）**、**依据与风险**、**确认项**三块配套内容。

适用：有数据文件（CSV/TSV/JSON/XLSX/MAT/曲线/PDF 论文）且需回答"数据说明什么、哪个点异常、可能为何、下一步复核什么、补做什么实验"。重点覆盖三类实际形态：①大量弯曲/时序曲线（如 197 条原始弯曲轨迹+样本工作簿/MATLAB）；②多折交叉验证/多估计器计算实验（如 5 折划分、2 估计器）；③多 sheet 工作簿+论文（如含主数据 12 个 sheet 的药学数据）。

不适用：无任何数据文件的纯理论问答、需合规签字的临床与安全结论、从零设计全新实验方案（本 Skill 只把方案作为解释依据补全）。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（案例元数据与约束，可能含 `case_id`/`id`/`challenge_id`、`output_contract`、材料清单）、`/input/task.md`（任务与评分要求）。
- 材料文件在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/MAT 等）。**必须实际读取文件内容，不得按文件名或标签臆断来源**；PDF 用平台 pdftotext 抽取正文，不依赖预置答案层。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并在 `degradations` 记录；坏 JSON 记录到 `errors` 后继续处理其余材料；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（临时文件），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`data_profile.json`、`figure_*.svg`。result.json 顶层字段：

- `case_id`（必填，来自 case.json 或 task.md 的 challenge_id）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `task_type`、`summary`（1–3 句平实结论）
- `interpretation`：`trends` / `anomalies` / `possible_causes` / `verification_steps` / `followup_experiments`（五段结论）
- `experiment_plan`：`hypothesis` / `testable_question` / `variables` / `controls` / `steps` / `parameters` / `resources` / `interpretation_criteria`
- `evidence_and_risk`：`evidence_basis` / `risk_and_anomaly_handling` / `assumptions`
- `confirmation_items`：`safety` / `ethics` / `equipment_availability` / `expert_confirmation`
- `sources` / `uncertainty` / `change_log` / `errors` / `degradations` / `limitations` / `pending_human` / `runtime`
- `fabrication_gate`：`must_not_invent` / `non_fabrication_checks`（禁编造门控，本版重点）

字段精确定义见 `references/output_contract.md`；未知值写 `null` 或 `[]`，不改名不删键。case.json 若含 `output_contract`，其全部字段必须**原样回填**（缺值给 `null`/`[]`）。每条结论对象统一含：`evidence_grade`（G0 不可知/G1 直接观测/G2 可推导/G3 假设推断）、`confidence`（高/中/低）、`confidence_basis`、`evidence` 数组（每项 `locator` + `quote` 逐字摘录）。`status` 必须诚实：材料不足写 `insufficient_evidence`，绝不编造数据、机理、重复数、DOI 或分数。

## 4. 执行流程（总 900 秒、64 次模型请求）

时间预算按 15 分钟 / ≤64 次请求规划；**前 3 分钟内必须先落盘一版合法 result.json 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。请求优先用于"读原文→提取事实→核对证据"，不用于无依据扩展。

- **P0 骨架落盘（0–3 分钟）**：读 case.json + task.md，解析 case_id，运行 `python scripts/main.py skeleton --input /input --output /app/output`；立即写出含全部顶层字段与 output_contract 字段的 result.json（`status=partial`，所有结论 `evidence_grade=G0`）与 report.md 骨架。输入无材料时 `status=insufficient_evidence` 并列出缺口。
- **P1 数据清点与画像（3–8 分钟）**：运行 `python scripts/inspect_data.py --input /input --output /app/output` 生成 `data_profile.json`：文件清单、每列样本量/缺失/均值/中位数/标准差、z-score/IQR/MAD 离群标记、单位不一致、超量程/饱和、坏编码、曲线型 vs 表格型 vs 交叉验证型分型。XLSX/MAT 需先用平台 pandas/openpyxl 导出 CSV 后再画像；导出失败的二进制文件记入 `degradations` 并跳过其数值画像。
- **P2 五段结论与证据映射（8–13 分钟）**：依据画像与原文逐条写 `interpretation`（见第 5 节），同时补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 locator+quote、证据分级与置信依据；每写一段就增量写回 result.json。
- **P3 自检与定稿（13–15 分钟）**：运行 `python scripts/main.py validate --input /app/output/result.json` 校验 schema 与证据完整性；运行 `python scripts/main.py report --input /app/output/result.json --output /app/output` 生成最终 report.md / evidence_map.json；补 `change_log`、`uncertainty`、`limitations`、`pending_human`，最后 `python scripts/main.py finalize --input /app/output/result.json` 写 runtime。

## 5. 五段结论规范（含证据分级）

每条结论写 `evidence_grade`：G1=可从原始文件逐字/逐行直接复算；G2=由 G1 经明确统计方法（写明方法与阈值）推出；G3=假设/推断，须列支持证据、排除证据与待验证步骤；G0=不可知，写 `not_reported` 或 `pending_human`，不写结论。

- **趋势 trends**：写方向（增/减/稳定/非单调/有拐点）、所依据区间、对照、拟合方式（线性/滚动均值/分段/手动目测）、幅度与单位。禁止把"读数复述"当趋势。
- **异常点 anomalies**：在样本/批次/通道/孔位/时间点维度定位对象；给观察值、期望值、偏离幅度、判定阈值（写明 z-score/IQR/MAD/Grubbs/手动及数值）、是否超阈值、与对照或重复的偏离关系。曲线型数据先抽特征（峰值、斜率、能量、平台期、拐点），再按曲线 ID/批次/通道绑定异常。
- **可能原因 possible_causes**：按证据强度/可检验性排序；每条写 `supporting_evidence`（支持）与 `excluding_evidence`（排除）、`confound_risk`（相关≠因果）、`testability`（能否用现有数据或一个实验判定）。机制猜测若无排除过程，只能标 G3 低置信并写明缺哪步验证。
- **复核步骤 verification_steps**：写成第二个人可直接执行的核查动作——回查具体文件/字段/行或 sheet、核对批次与设备日志、加做对照或平行重复；每条写 `target`、`expected_if_supported`、`decision_rule`。禁止"检查数据质量"这类不可执行表述。
- **后续实验建议 followup_experiments**：对应原因候选与证据缺口写具体改动（补对照、加密采样、调参数范围、扩重复数），并写 `reason` 与 `expected_outcome`。

## 6. 不确定性分级与禁编造门控（本版核心）

- **不确定性显式化**：单位不一致、方法/阈值歧义、对照缺失、重复数不足、超量程或饱和、多来源冲突，必须逐条写入 `uncertainty`（含 `type`、`locator`、`impact`、`resolution_if_any`），并把对结论的影响写进 `limitations`。不擅自统一单位、不无依据插补。
- **置信依据**：每条结论的 `confidence_basis` 必须说明置信由什么支撑（观测样本量、重复数、对照是否齐全、阈值是否先验）。缺对照/重复不足时只能降为低置信。
- **禁编造门控**：`fabrication_gate.must_not_invent` 列出禁止补造项（湿实验读数、异常原因、p 值、文献 DOI、benchmark 分数、设备日志）；`non_fabrication_checks` 逐条核对"该值是否在材料中逐字出现 / 是否实际计算 / 是否来自平台"。无法核对就写 `not_reported`/`pending_human`，绝不写成事实。

## 7. 证据溯源

每条关键结论/数值/图元都要能指回材料逐字片段（locator+quote）。同一指标两来源冲突时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。单位、方法、阈值本身有歧义时保留歧义，不擅自统一。

## 8. 脚本调用（相对路径）

```bash
python scripts/main.py skeleton --input /input --output /app/output
python scripts/inspect_data.py --input /input --output /app/output
python scripts/main.py report --input /app/output/result.json --output /app/output
python scripts/main.py validate --input /app/output/result.json
python scripts/main.py finalize --input /app/output/result.json
python scripts/selftest.py --root . --input ./demo/input --output ./demo/output
```

脚本仅用 Python 标准库；在 Skill 根目录下执行，`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。失败时脚本退出码非 0 并在 stderr 说明；Agent 不得因脚本失败而编造结果。

## 9. 自检清单（提交/定稿前逐条勾选）

- [ ] result.json 可被 `json.load` 解析，`case_id` 非空，`status` 合法
- [ ] output_contract 全部字段存在（缺值 `null`/`[]`），不改名不删键
- [ ] 五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（locator+quote）、`evidence_grade`、`confidence`、`confidence_basis`
- [ ] 异常点写明阈值方法+数值，原因写明支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（含 target/判据），无空话
- [ ] 单位、方法、阈值歧义被保留并写入 `uncertainty`
- [ ] 缺失材料、坏编码、超量程、对照缺失、重复数不足写入 `limitations`/`degradations`
- [ ] `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填；未经专家确认事项进 `pending_human`
- [ ] `fabrication_gate.non_fabrication_checks` 已逐条核对，未补造任何禁造项
- [ ] `change_log` 记录骨架→画像→结论→定稿各阶段；`runtime` 含耗时
- [ ] 重复运行同一输入结果一致（确定性排序、无随机数依赖）

## 10. 降级策略

- 无数据/空输入 → `status=insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON/坏编码 → 记录 `errors`/`degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，在 `uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`，不假装可下结论。
- 缺软件/缺依赖 → 如实标注 limitations；本 Skill 脚本只依赖标准库，XLSX/MAT 需平台 pandas/openpyxl 导出 CSV，导出失败则记录并跳过数值画像。
- **严禁编造评测分数、机理、重复数或运行结果**；无法判断就写 `not_reported`/`pending_human`。

## 11. 最小调用示例（示意，非正式案例）

```text
输入：/input/case.json（case_id=...）、/input/materials/data.csv（3 列：sample, time_s, reading）
调用：python scripts/main.py skeleton --input /input --output /app/output
      python scripts/inspect_data.py --input /input --output /app/output
示意输出（片段，标注为示例）：
result.json → {"case_id":"...","status":"partial","interpretation":{"trends":[],...},
  "fabrication_gate":{"must_not_invent":[...]},"change_log":[...]}
report.md → "# 结果解释（partial）\n## 趋势\n- （示例占位，未填入真实读数）"
```

以上输出仅为接口示意，不含任何正式案例答案。

## 12. 边界

一个 Skill 只解决"实验结果解释与异常分析"这一类任务，不扩展为通用分析平台。没有真实材料时输出"材料不足"而非示例填充。交付的 result.json 是运行时产物，Skill 包内不得预置正式案例答案或白板。
