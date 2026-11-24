---
name: experiment-result-interpretation
description: 科研实验结果解释与异常分析 Skill：把实验读数、曲线、图谱、设备日志与论文材料转成趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论，并补全实验方案与风险判据；输出结构化 result.json 与可编辑 report.md，内置鲁棒降级（空输入、坏 JSON、坏编码、缺参数、超大文件均不崩溃）。当需要解读实验数据、判定异常、规划复核或补做实验时使用。
version: 1.1.0
---

# 实验结果解释与异常分析

## 1. 角色与何时使用

本 Skill 面向“一轮实验跑完后”的解释工作：把原始读数、时序/弯曲曲线、多批次多通道数据、图谱、设备日志与论文材料，转化为五段可检查结论——**趋势、异常点、可能原因、复核步骤、后续实验建议**，并据题面补全**实验方案**（假设、变量、对照、步骤、参数、资源、判读标准）、**依据与风险**、**确认项**。适用于有数据文件（CSV/TSV/JSON/表格/XLSX/MAT/曲线/PDF 论文）且需回答“数据说明什么趋势、哪个点异常、可能为何、下一步复核什么、补做什么实验”的场景。不适用于：无任何数据文件的纯理论问答、需要合规签字的临床与安全结论、从零设计全新实验方案（本 Skill 只把方案作为解释依据补全）。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（案例元数据与约束，可能含 `case_id`/`id`/`challenge_id`、`output_contract`、材料清单）、`/input/task.md`（任务与评分要求）。
- 材料文件在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/MAT 等）。**必须实际读取文件内容**，不得按文件名或标签臆断来源。
- 文本按编码阶梯读取：`utf-8-sig` → `utf-8` → `gbk` → `latin-1`（UTF-16 仅按 BOM 识别），坏编码降级并在 `degradations` 记录；含 NUL 字节视为二进制；坏 JSON 记入 `errors` 后继续处理其余材料；单文件 >2MB 跳过并记录（阈值见 `scripts/main.py` 的 `MAX_FILE_BYTES`）。
- 工作目录 `/app/work/`（临时文件），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`data_profile.json`、`figure_*.svg`。result.json 顶层字段（契约精确定义见 `references/output_contract.md`）：

- `case_id`（必填，来自 case.json 或 task.md 的 challenge_id）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `task_type`、`summary`（1–3 句平实结论）
- `interpretation`：`trends` / `anomalies` / `possible_causes` / `verification_steps` / `followup_experiments`
- `experiment_plan`：`hypothesis` / `testable_question` / `variables` / `controls` / `steps` / `parameters` / `resources` / `interpretation_criteria`
- `evidence_and_risk`：`evidence_basis` / `risk_and_anomaly_handling` / `assumptions`
- `confirmation_items`：`safety` / `ethics` / `equipment_availability` / `expert_confirmation`
- `sources` / `uncertainty` / `change_log` / `errors` / `degradations` / `limitations` / `pending_human` / `runtime`

若 `case.json` 含 `output_contract`（`fields`/`required`/`schema` 列表或键值对），**必须原样回填其全部键**：缺值写 `null`/`[]`，不改名、不删未知键。每条关键结论必须带 `evidence` 数组，每项含 `locator`（如 `materials/paper.md#p3`、`data/batch_12.csv#L34`）+ `quote`（逐字摘录）。`status` 必须诚实：材料不足写 `insufficient_evidence`，绝不编造数据、机理或重复数。

## 4. 执行流程（总 900 秒、64 次模型请求）

时间预算按 15 分钟 / ≤64 次请求规划；**前 3 分钟内必须先落盘一版合法 result.json 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。

- **P0 骨架落盘（0–3 分钟）**：读 case.json + task.md，解析 case_id，运行 `python scripts/main.py skeleton --input /input --output /app/output`；立即写出含全部顶层字段的 result.json（`status=partial`）与 report.md 骨架。若输入为空则 `status=insufficient_evidence` 并记录缺口。
- **P1 数据清点与画像（3–8 分钟）**：运行 `python scripts/inspect_data.py --input /input --output /app/output` 生成 `data_profile.json`（文件清单、每列样本量/缺失/均值/中位数/标准差、z-score/IQR/MAD 离群标记、单位不一致、超量程/饱和、坏编码）。定位每个数值到具体文件/列/行。XLSX/MAT 需先用平台 pandas/openpyxl/scipy 导出 CSV 后再画像；若平台工具缺失，脚本如实标注并降级为文本解析。
- **P2 五段结论与证据映射（8–13 分钟）**：依据画像与原文逐条写 `interpretation`（见第 5 节），同时补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 locator+quote；每写一段就增量写回 result.json。
- **P3 自检与定稿（13–15 分钟）**：运行 `python scripts/main.py validate --input /app/output/result.json` 校验 schema 与证据完整性；运行 `python scripts/main.py report --input /app/output/result.json --output /app/output` 生成最终 report.md / evidence_map.json；补 `change_log`、`uncertainty`、`limitations`、`pending_human`，最后 `python scripts/main.py finalize --input /app/output/result.json` 写 runtime。

每阶段结束都做增量落盘；模型请求优先用于“读原文→提取事实→核对证据”，不用于无依据扩展。64 次请求是上限，不是目标。

## 5. 五段结论规范

- **趋势 trends**：写方向（增/减/稳定/非单调/有拐点/平台期）、所依据区间、对照、拟合方式（线性/滚动均值/手动目测）、幅度与单位。禁止把“读数复述”当趋势，必须给出方向与拐点判断。
- **异常点 anomalies**：在样本/批次/通道/孔位/时间点维度定位对象；给出观察值、期望值、偏离幅度、判定阈值（写明 z-score/IQR/MAD/Grubbs/手动及其数值）、是否超阈值、相对对照或重复的偏离关系。
- **可能原因 possible_causes**：按证据强度/可检验性排序；每条写 `supporting_evidence`（支持）与 `excluding_evidence`（排除）、`confound_risk`（相关≠因果）、`testability`（能否用现有数据或一个实验判定）。机制猜测若无排除过程，只能标低置信并写明缺哪步验证。
- **复核步骤 verification_steps**：写成第二个人可直接执行的核查动作——回查具体文件/字段/行或 sheet、核对批次与设备日志、加做对照或平行重复；每条写 `target`、`expected_if_supported`、`decision_rule`。禁止“检查数据质量”这类不可执行表述。
- **后续实验建议 followup_experiments**：对应原因候选与证据缺口写具体改动（补对照、加密采样、调参数范围、扩重复数），并写 `reason` 与 `expected_outcome`。

## 6. 证据溯源

每条关键结论/数值/图元都要能指回材料逐字片段（locator+quote）。同一指标两来源冲突时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。单位、方法、阈值本身有歧义时保留歧义，不擅自统一。

## 7. 脚本调用（相对路径，在 Skill 根目录执行）

```bash
python scripts/main.py skeleton --input /input --output /app/output
python scripts/inspect_data.py --input /input --output /app/output
python scripts/main.py report --input /app/output/result.json --output /app/output
python scripts/main.py validate --input /app/output/result.json
python scripts/main.py finalize --input /app/output/result.json
python scripts/selftest.py
```

脚本仅用 Python 标准库；`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。失败时脚本退出码非 0 并在 stderr 说明；Agent 不得因脚本失败而编造结果。

## 8. 自检清单（提交/定稿前逐条勾选）

- [ ] result.json 可被 `json.load` 解析，`case_id` 非空，`status` 合法
- [ ] 五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（locator+quote），可回指具体文件/字段/行
- [ ] 异常点写明阈值方法+数值，原因写明支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（含 target/判据），无空话
- [ ] 单位、方法、阈值歧义被保留并写入 `uncertainty`
- [ ] 缺失材料、坏编码、超量程、对照缺失、重复数不足被识别并写入 `limitations`/`degradations`
- [ ] `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填；未经专家确认事项进 `pending_human`，不写成事实
- [ ] `change_log` 记录骨架→画像→结论→定稿各阶段；`runtime` 含耗时
- [ ] 重复运行同一输入结果一致（确定性排序、无随机数依赖）
- [ ] `python scripts/selftest.py` 全绿（空输入/坏 JSON/坏编码/超大文件/缺参数各场景）

## 9. 降级策略（鲁棒性重点）

本版侧重点是**优雅降级不崩溃**，遵循“发现 → 记录 → 降级 → 继续”四步：

- 空输入/无数据 → `status=insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON → 记入 `errors`；坏编码 → 记入 `degradations`，均继续处理其余材料。
- 缺参数 → `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；未知键不改名不删除。
- 超大文件（>2MB）→ 跳过并记入 `degradations`，其余文件照常处理。
- 二进制文件（含 NUL 字节）→ 标记 `binary`，按类型处理或记录，不做无依据文本解析。
- 单位不一致/读数超量程或饱和 → 保留原始单位，在 `uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`，不假装可下结论。
- 缺软件/缺依赖 → 如实标注 limitations；本 Skill 脚本只依赖标准库，若平台额外工具（pandas/openpyxl/scipy）缺失则说明并用标准库兜底。
- 写盘失败 → 捕获异常，降级到最小合法产物并写入 `errors`，不让进程崩溃。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断就写 `not_reported`/`pending_human`。

## 10. 边界

一个 Skill 只解决“实验结果解释与异常分析”这一类任务，不扩展为通用分析平台。没有真实材料时输出“材料不足”而非示例填充。交付的 result.json 是运行时产物，Skill 包内不得预置正式案例答案或白板。
