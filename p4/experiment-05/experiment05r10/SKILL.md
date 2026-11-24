---
name: experiment-result-interpretation
description: 科研实验结果解释与异常分析 Skill：读取原始读数、时序/弯曲曲线、多批次多通道表格、图谱与设备日志，先用脚本生成事实画像 facts.json（逐列统计与候选异常，只产事实不产结论），再由 Agent 产出趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论，输出结构化 result.json 与可编辑 report.md，支持证据溯源、异常定位、原因排序与可执行复核清单。适用于实验数据解释、异常判定、复核与补做实验规划。
version: 2.0.0
---

# 实验结果解释与异常分析

## 1. 角色与何时使用

本 Skill 面向"一轮实验跑完后的解释"：把原始读数、时序/弯曲曲线、多批次多通道数据、交叉验证结果、图谱与设备日志，转化为五段可检查结论——趋势、异常点、可能原因、复核步骤、后续实验建议。适用于 /input 下确有材料（CSV/TSV/JSON/表格/曲线/PDF/Excel/MAT），且需回答"数据说明什么趋势、哪个点异常、离正常多远、可能为何、下一步复核什么、补做什么实验"的场景。

不适用于：无任何材料的纯理论问答；需合规签字的临床/安全结论；从零设计全新实验方案（本 Skill 只把方案作为解释依据与缺口补全）。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（含 `case_id`/`id`/`challenge_id`、可能的 `output_contract`、材料清单）与 `/input/task.md`（任务与评分要求）。
- 材料在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/MAT 等）。**必须实际读取文件内容**，不得按文件名或标签臆断来源；PDF 用平台 pdftotext 解析原文（无预置答案层）。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并记入 `degradations`；坏 JSON 记入 `errors` 后继续处理其余材料；单文件 >2MB 跳过并记录。
- XLSX 每 sheet、MAT 每个变量先用平台 pandas/openpyxl/scipy 导出为 CSV 到 `/app/work/`，再画像；导出时保留原始单位与列名。
- 工作目录 `/app/work/`（临时文件），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`facts.json`。result.json 顶层字段：

- `case_id`（必填，取 case.json 或 task.md 的 challenge_id）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `task_type`、`summary`（1–3 句平实结论）
- `interpretation`：`trends` / `anomalies` / `possible_causes` / `verification_steps` / `followup_experiments`
- `experiment_plan`：`hypothesis` / `testable_question` / `variables` / `controls` / `steps` / `parameters` / `resources` / `interpretation_criteria`
- `evidence_and_risk`：`evidence_basis` / `risk_and_anomaly_handling` / `assumptions`
- `confirmation_items`：`safety` / `ethics` / `equipment_availability` / `expert_confirmation`
- `sources` / `uncertainty` / `change_log` / `errors` / `degradations` / `limitations` / `pending_human` / `runtime`

`case.json` 含 `output_contract` 时，必须**原样回填其全部字段**（缺值写 `null`/`[]`），不改名不删未知键。字段精确定义见 `references/output_contract.md`。每条关键结论必须带 `evidence` 数组，每项含 `locator`（如 `data/batch_12.csv#R34.C3`）+ `quote`（逐字摘录）。`status` 必须诚实：材料不足写 `insufficient_evidence`，绝不编造数据、机理或重复数。

## 4. 执行流程（总 900 秒、64 次模型请求）

时间预算 15 分钟 / ≤64 次请求；**前 3 分钟内必须先落盘一版合法 result.json 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。模型请求优先用于"读原文→提取事实→核对证据"，不用于无依据扩展。

- **P0 骨架落盘（0–3 分钟）**：读 case.json + task.md，解析 case_id，运行 `python scripts/main.py skeleton --input /input --output /app/output`，立即得到含全部顶层字段的 result.json（status=`partial`）与 report.md 骨架；空输入则 status=`insufficient_evidence`。
- **P1 事实画像（3–8 分钟）**：把 XLSX/MAT 导出为 `/app/work/` 下的 CSV，运行 `python scripts/main.py profile --input /input --work /app/work --output /app/output` 生成 `facts.json`（文件清单、逐列 n/缺失/均值/中位数/标准差、z/IQR/MAD 离群候选、饱和与单位提示、坏编码记录）。**facts.json 只含事实，不含结论**。
- **P2 五段结论（8–13 分钟）**：读取 facts.json 与材料原文，逐条写 `interpretation`（见第 5 节），并补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 locator+逐字 quote；每写一段就增量写回 result.json，避免最后集中返工。
- **P3 校验定稿（13–15 分钟）**：运行 `python scripts/main.py validate --input /app/output/result.json` 校验 schema/契约与证据完整性；`python scripts/main.py report --input /app/output/result.json --output /app/output` 生成最终 report.md 与 evidence_map.json；补 `change_log`、`uncertainty`、`limitations`、`pending_human`；最后 `python scripts/main.py finalize --input /app/output/result.json` 写 runtime。

每阶段结束都增量落盘。脚本退出码非 0 时读 stderr 修正，Agent 不得因脚本失败而编造结果。

## 5. 五段结论规范

- **trends**：写方向（增/减/稳定/非单调/有拐点）、所依据区间、对照、拟合方式（线性/滚动均值/手动目测）、幅度与单位。禁止把"读数复述"当趋势。
- **anomalies**：在样本/批次/通道/孔位/时间点维度定位对象；写观察值、期望值、偏离幅度、判定阈值（z-score/IQR/MAD/Grubbs/手动，含数值）、是否超阈值、与对照或重复的偏离关系。
- **possible_causes**：按证据强度/可检验性排序；每条写 `supporting_evidence` 与 `excluding_evidence`、`confound_risk`、`testability`。机制猜测若无排除过程，标低置信并写明缺哪步验证。
- **verification_steps**：写成第二个人可直接执行的核查动作，每条写 `target`、`expected_if_supported`、`decision_rule`。禁止"检查数据质量"这类空话。
- **followup_experiments**：对应原因候选与证据缺口写具体改动（补对照、加密采样、调参数范围、扩重复数），每条写 `reason` 与 `expected_outcome`。

数据形态提示（按形状处理，不预设答案）：时序/弯曲曲线用逐列趋势 + 逐格离群扫描定位"轨迹×时间点"；多批次/多通道先按 batch/channel 分组再统计；交叉验证结果（如 5-split/多估计器）逐 split 报告指标并指出跨 split 波动与过拟合信号；多 sheet 工作簿逐 sheet 导出后与论文原文对照，冲突处两条都记录并标 `source_conflict`。

## 6. 证据溯源

每条关键结论/数值/图元都要回指材料逐字片段（locator+quote）。`facts.json` 用 `#R行.C列` 定位；引用原文时用文件真实行号 `#L行` 并逐字摘录。同一指标两来源冲突时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`；单位、方法、阈值歧义保留原样写入 `uncertainty`，不擅自统一。

## 7. 脚本调用（相对路径，在 Skill 根目录执行）

```bash
python scripts/main.py skeleton --input /input --output /app/output
python scripts/main.py profile  --input /input --work /app/work --output /app/output
python scripts/main.py validate --input /app/output/result.json
python scripts/main.py report   --input /app/output/result.json --output /app/output
python scripts/main.py finalize --input /app/output/result.json
python scripts/selftest.py
```

脚本仅用 Python 标准库；`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。`main.py` 只做分发，实现与校验顺序见 `scripts/core_impl.py`。

## 8. 自检清单（定稿前逐条勾选）

- [ ] result.json 可被 `json.load` 解析，`case_id` 非空，`status` 合法
- [ ] 五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（locator+quote），可回指具体文件/字段/行
- [ ] 异常点写明阈值方法+数值；原因写明支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（含 target/expected_if_supported/decision_rule）
- [ ] 单位、方法、阈值歧义被保留并写入 `uncertainty`
- [ ] 缺失材料、坏编码、超量程、对照缺失、重复数不足写入 `limitations`/`degradations`
- [ ] `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填；未经专家确认事项进 `pending_human`
- [ ] `output_contract` 全部字段已回填，未改名删键
- [ ] `change_log` 记录骨架→画像→结论→定稿；`runtime` 含耗时
- [ ] 重复运行同一输入结果一致（确定性排序、无随机数依赖，runtime 除外）

## 9. 降级策略

- 无数据/空输入 → status=`insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON/坏编码 → 记录 `errors`/`degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，写入 `uncertainty`，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`，不假装可下结论。
- 缺软件/缺依赖 → 如实标注 `limitations`；本 Skill 脚本只依赖标准库，XLSX/MAT/PDF 用平台工具导出后兜底。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断就写 `not_reported`/`pending_human`。

## 10. 边界

一个 Skill 只解决"实验结果解释与异常分析"这一类任务。没有真实材料时输出"材料不足"而非示例填充。交付的 result.json 是运行时产物，Skill 包内不预置正式案例答案或白板。
