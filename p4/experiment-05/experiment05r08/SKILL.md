---
name: experiment-result-interpretation-v2
description: 科研实验结果解释与异常分析 Skill 的评分维度对齐升级版：按数据形态（曲线/表格/多表工作簿+论文）分派解释模板，把实验读数、曲线、图谱、多批次表格与设备日志转化为趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论，每条结论绑定 locator 与逐字 quote，输出含全部 output_contract 字段的结构化 result.json 与可编辑报告，并附评分维度自检。当需要解读实验数据、判定异常或规划复核与补做实验时使用。
version: 2.0.0
---

# 实验结果解释与异常分析（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 面向“一轮实验跑完后”的解释工作：把原始读数、时序/弯曲曲线、多批次多通道表格、图谱与设备日志，转化为**趋势、异常点、可能原因、复核步骤、后续实验建议**五段可检查结论。适用：有数据文件（CSV/TSV/JSON/表格/曲线/PDF 论文/工作簿）且需回答“趋势是什么、哪个点异常、可能为何、复核什么、补做什么”的场景。不适用：无任何数据文件的纯理论问答、需合规签字的临床与安全结论、从零设计全新实验方案（本 Skill 仅把方案作为解释依据补全）。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（案例元数据、约束、可能含 `output_contract`、材料清单）、`/input/task.md`（任务与评分要求）。材料文件在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/MAT 等）。
- 必须实际读取文件内容，不得按文件名或标签臆断来源；PDF 解析拿到的是原始 PDF，无预提取答案层。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并记入 `degradations`；坏 JSON 记入 `errors` 后继续处理其余材料；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（临时文件），最终产物写 `/app/output/`。XLSX/MAT 先用平台 pandas/openpyxl 导出 CSV 再画像。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`data_profile.json`、`figure_*.svg`。result.json 顶层字段：`case_id`（必填）、`status`、`task_type`、`summary`、`data_form`、`interpretation`（trends/anomalies/possible_causes/verification_steps/followup_experiments）、`experiment_plan`（hypothesis/testable_question/variables/controls/steps/parameters/resources/interpretation_criteria）、`evidence_and_risk`（evidence_basis/risk_and_anomaly_handling/assumptions）、`confirmation_items`（safety/ethics/equipment_availability/expert_confirmation）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`。字段精确定义见 `references/output_contract.md`；未知值写 `null` 或 `[]`，不改名不删键。若 case.json 含 `output_contract`，其全部字段**原样回填**。每条关键结论绑定 `evidence` 数组，每项含 `locator`（如 `materials/paper.md#p3`、`data/batch_12.csv#L34`）+ `quote`（逐字摘录）。`status` 必须诚实：`ok | partial | insufficient_evidence | input_error`，绝不编造数据、机理、重复数、软件状态或评测分数。

## 4. 执行流程（总 900 秒、≤64 次模型请求）

时间预算按 15 分钟规划；**前 3 分钟内必须先落盘一版合法 partial 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。

- **P0 骨架落盘（0–3 分钟）**：读 case.json + task.md，解析 case_id，运行 `python scripts/main.py skeleton --input /input --output /app/output`；立即写出含全部顶层字段的 result.json（status=`partial`）与 report.md 骨架。若输入为空则 status=`insufficient_evidence` 并记录缺口。
- **P1 数据清点与画像（3–8 分钟）**：运行 `python scripts/main.py inspect --input /input --output /app/output` 生成 `data_profile.json`（文件清单、每列 n/缺失/均值/中位数/标准差/分位数/IQR/MAD、z/IQR/MAD/Grubbs 离群标记、曲线特征、坏编码/超限/饱和降级）。依据 `data_form` 分派解释模板（见 `references/scoring_alignment.md` 第 2 节）。
- **P2 五段结论与证据映射（8–13 分钟）**：依画像与原文逐条写 `interpretation`（见第 5 节），同时补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 locator+quote，每写一段就增量写回 result.json。
- **P3 自检与定稿（13–15 分钟）**：运行 `python scripts/main.py validate --input /app/output/result.json` 校验 schema 与证据完整性；运行 `python scripts/main.py build --input /app/output/result.json --output /app/output` 生成最终 report.md / evidence_map.json；补 `change_log`、`uncertainty`、`limitations`、`pending_human`，最后 `python scripts/main.py finalize --input /app/output/result.json` 写 runtime。

模型请求优先用于“读原文→提取事实→核对证据”，不用于无依据扩展；64 次是上限不是目标。每阶段结束都增量落盘。

## 5. 五段结论规范

- **趋势 trends**：写方向（增/减/稳定/非单调/有拐点）、依据区间、对照、拟合方式（线性/滚动均值/手动目测）、幅度与单位。禁止把“读数复述”当趋势。
- **异常点 anomalies**：定位到样本/批次/通道/孔位/时间点；给观察值、期望值、偏离幅度、判定阈值（写明 z/IQR/MAD/Grubbs 方法+数值）、是否超阈值、与对照或重复的偏离关系。
- **可能原因 possible_causes**：按证据强度/可检验性排序；每条写 `supporting_evidence`（支持）与 `excluding_evidence`（排除）、`confound_risk`（相关≠因果）、`testability`。机制猜测若无排除过程，只能标低置信并写明缺哪步验证。
- **复核步骤 verification_steps**：写成第二个人可直接执行的核查动作——回查具体文件/字段/行或 sheet、核对批次与设备日志、加做对照或平行重复；每条写 `target`、`expected_if_supported`、`decision_rule`。禁止“检查数据质量”类空话。
- **后续实验建议 followup_experiments**：对应原因候选与证据缺口写具体改动（补对照、加密采样、调参数范围、扩重复数），并写 `reason` 与 `expected_outcome`。

## 6. 证据溯源

每条关键结论/数值/图元都指回材料逐字片段（locator+quote）。同一指标两来源冲突时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。单位、方法、阈值本身有歧义时保留歧义，不擅自统一。

## 7. 脚本调用（相对路径）

```bash
python scripts/main.py skeleton --input /input --output /app/output
python scripts/main.py inspect   --input /input --output /app/output
python scripts/main.py build     --input /app/output/result.json --output /app/output
python scripts/main.py validate  --input /app/output/result.json
python scripts/main.py finalize  --input /app/output/result.json
python scripts/selftest.py
```

脚本仅用 Python 标准库；在 Skill 根目录执行，`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。失败时退出码非 0 并在 stderr 说明；Agent 不得因脚本失败而编造结果。

## 8. 自检清单（提交/定稿前逐条勾选，编号对应评分维度）

- [ ] **S1** SKILL 可被正确触发，frontmatter 含 name/description/version，脚本相对路径可执行
- [ ] **S2** result.json 可被 `json.load` 解析，`case_id` 非空，`status` 合法，五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] **S3** 每条结论带 evidence（locator+quote），可回指具体文件/字段/行
- [ ] **S4** 异常点写明阈值方法+数值，趋势写明方向+区间+拟合方式
- [ ] **S5** 每条数值结论带复算配方（方法、窗口、样本量、阈值），他人可复算
- [ ] **S6** 单位不一致、超量程/饱和、对照缺失、重复数不足被识别并写入 `uncertainty`/`limitations`/`degradations`
- [ ] **S7** 覆盖全部指标/对照/重复/边界情况，未解释部分如实交代
- [ ] **S8** 异常绑定到批次/通道/孔位维度，原因按可检验性排序并写支持/排除证据与 confound_risk
- [ ] **S9** 复核步骤为可执行动作（含 target/expected/decision_rule），无空话；创新点用当前案例数据验证边界
- [ ] **S10** 骨架先落盘、增量写、确定性排序，重复运行结果一致
- [ ] **S11** `change_log` 记录骨架→画像→结论→定稿各阶段；`runtime` 含耗时
- [ ] **S12** `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填，字段含义齐全
- [ ] **S13** 空输入/坏 JSON/坏编码/超限均不崩溃，退出码 0 并写合法产物
- [ ] **S14** 未经专家确认事项进 `pending_human`，不写成事实；阈值配置与复核清单可复用

## 9. 降级策略

- 无数据/空输入 → status=`insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON/坏编码 → 记录 `errors`/`degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，在 `uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`，不假装可下结论。
- 缺软件/缺依赖 → 如实标注 limitations；本 Skill 脚本只依赖标准库，XLSX/MAT 需平台 pandas 先导出 CSV。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断就写 `not_reported`/`pending_human`。

## 10. 边界

一个 Skill 只解决“实验结果解释与异常分析”这一类任务，不扩展为通用分析平台。没有真实材料时输出“材料不足”而非示例填充。交付的 result.json 是运行时产物，Skill 包内不得预置正式案例答案或白板。
