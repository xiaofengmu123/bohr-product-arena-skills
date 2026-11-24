---
name: experiment-result-interpretation
description: 精简版实验结果解释 Skill：把表格读数、时序/弯曲曲线、图谱与设备日志转成趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论，自动画像数据形态（trace/tabular/experiment）并输出 result.json 与可编辑报告；用于解读实验数据、定位异常、规划复核与补做实验。
version: 1.1.0
---

# 实验结果解释与异常分析（精简版）

## 1. 角色与何时使用

本 Skill 把“一轮实验跑完后”的读数、曲线、图谱与设备日志，转成五段可复核结论——趋势、异常点、可能原因、复核步骤、后续实验建议，并补实验方案（假设/变量/对照/步骤/参数/资源/判读标准）与风险、确认项。适用于有真实数据文件（CSV/TSV/JSON/表格/曲线/PDF）且需回答“什么趋势、哪个点异常、可能为何、下一步复核什么、补做什么实验”的场景。不适用：无数据文件的纯理论问答、需合规签字的临床/安全结论、从零设计全新实验方案。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（含 case_id/id/challenge_id/armiId、`output_contract`、材料清单）、`/input/task.md`；材料在 `/input/` 任意子目录（materials/、data/、PDF/CSV/XLSX/MAT）。
- 必须实际读取文件内容，不得按文件名或标签臆断来源；PDF 用 pdftotext，XLSX/MAT 先用平台 pandas/scipy 导出为 CSV/文本到 `/app/work/` 再画像。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并记入 `degradations`；坏 JSON 记入 `errors` 后继续处理；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（临时文件），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`（主产物）与 `/app/output/report.md`（可编辑），可选 `evidence_map.json`、`data_profile.json`、`data_profile.md`。

result.json 顶层必含：`case_id`（来自 case.json/task.md）、`status`（ok|partial|insufficient_evidence|input_error）、`task_type`、`summary`（1–3 句平实结论）、`interpretation`（trends/anomalies/possible_causes/verification_steps/followup_experiments）、`experiment_plan`（hypothesis/testable_question/variables/controls/steps/parameters/resources/interpretation_criteria）、`evidence_and_risk`（evidence_basis/risk_and_anomaly_handling/assumptions）、`confirmation_items`（safety/ethics/equipment_availability/expert_confirmation）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`。

若 case.json 含 `output_contract`，其声明的全部字段必须原样回填（缺值 null/[]），不改名、不删未知键；字段语义见 `references/output_contract.md`。每条关键结论必须带 `evidence` 数组，每项含 `locator`（如 `materials/paper.md#p3`、`data/batch.csv#L34`）+ `quote`（逐字摘录）。`status` 必须诚实：材料不足写 `insufficient_evidence`，绝不编造数据、机理或重复数。

## 4. 执行流程（总 900 秒、≤64 次模型请求）

预算：≤64 次请求、900 秒；**前 3 分钟内必须先落盘合法 result.json 骨架，之后只做增量改进**，任何中断都留下可读 partial 结果。模型请求优先用于“读原文→提取事实→核对证据”，不用于无依据扩展。

- **P0 骨架（0–3 分钟）**：读 case.json + task.md，`python scripts/main.py skeleton --input /input --output /app/output --work /app/work`，立即写出含全部顶层字段与 output_contract 空值的 result.json（status=partial）与 report.md 骨架。空输入则 status=insufficient_evidence 并列缺口。
- **P1 数据清点与画像（3–8 分钟）**：先用平台工具把 XLSX/MAT/PDF 导出到 `/app/work/`（CSV/文本），再 `python scripts/profile.py --input /input --work /app/work --output /app/output`，生成 `data_profile.json`（文件清单、数据形态 trace/tabular/experiment、每列 n/缺失/均值/中位数/标准差/IQR/MAD、z-score/IQR/MAD 离群、单位冲突、超量程/饱和、坏编码）。定位每个数值到具体文件/列/行。
- **P2 五段结论与证据映射（8–13 分钟）**：依据画像与原文逐条写 `interpretation`（见第 5 节），同时补 `experiment_plan`、`evidence_and_risk`、`confirmation_items`；每条结论绑定 locator+quote。增量写回用 `python scripts/main.py set --file /app/output/result.json --key interpretation.trends --value '<JSON>' --append`，避免手改嵌套 JSON 出错；每写一段即落盘。
- **P3 自检与定稿（13–15 分钟）**：`python scripts/main.py validate --input /app/output/result.json` 校验 schema 与证据完整性；`python scripts/main.py report --input /app/output/result.json --output /app/output` 生成 report.md / evidence_map.json；补 `change_log`、`uncertainty`、`limitations`、`pending_human`；最后 `python scripts/main.py finalize --input /app/output/result.json` 写 runtime。

## 5. 五段结论规范（压缩）

| 段 | 必写内容 | 禁止 |
|-|-|-|
| trends 趋势 | 方向（增/减/稳定/非单调/拐点）+ 所依据区间 + 对照 + 拟合方式 + 幅度与单位 | 读数复述、无方向 |
| anomalies 异常点 | 定位到样本/批次/通道/孔位/时间点；观察值、期望值、偏离幅度、判定阈值（写 z-score/IQR/MAD/Grubbs/手动及数值）、是否超阈值、与对照/重复的偏离 | 只说“偏高”不给阈值 |
| possible_causes 可能原因 | 按证据强度/可检验性排序；每条 supporting_evidence + excluding_evidence + confound_risk + testability | 只给无排除过程的机理猜测 |
| verification_steps 复核步骤 | 第二人可直接执行：回查文件/字段/行或 sheet、核对批次与设备日志、加对照/平行重复；每条 target + expected_if_supported + decision_rule | “检查数据质量”等空话 |
| followup_experiments 后续实验建议 | 对应原因候选与证据缺口的具体改动（补对照/加密采样/调参数范围/扩重复数）+ reason + expected_outcome | 与证据缺口无关的泛泛建议 |

## 6. 证据溯源

每条关键结论/数值/图元都要指回材料逐字片段（locator+quote）。同一指标两来源冲突时两条都记录并标 `source_conflict`。不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。单位、方法、阈值本身有歧义时保留歧义，不擅自统一，写入 `uncertainty`。

## 7. 脚本调用（相对路径，纯标准库，Skill 根目录执行）

```bash
python scripts/main.py skeleton --input /input --output /app/output --work /app/work
python scripts/profile.py --input /input --work /app/work --output /app/output
python scripts/main.py set --file /app/output/result.json --key <dot.path> --value '<json>' [--append]
python scripts/main.py validate --input /app/output/result.json
python scripts/main.py report --input /app/output/result.json --output /app/output
python scripts/main.py finalize --input /app/output/result.json
python scripts/selftest.py        # 自检：空输入/坏JSON/小CSV/确定性/validate
```

脚本失败退出码非 0 并在 stderr 说明；Agent 不得因脚本失败而编造结果。

## 8. 自检清单（定稿前逐条勾选）

- [ ] result.json 可 `json.load`，`case_id` 非空，`status` 合法
- [ ] 五段 `interpretation` 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（locator+quote），可回指文件/字段/行
- [ ] 异常点写阈值方法+数值；原因写支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（target/判据），无空话
- [ ] 单位/方法/阈值歧义保留并写入 `uncertainty`
- [ ] 缺失材料/坏编码/超量程/对照缺失/重复数不足写入 `limitations`/`degradations`
- [ ] `experiment_plan`/`evidence_and_risk`/`confirmation_items` 已填；未确认项进 `pending_human`
- [ ] `change_log` 记录骨架→画像→结论→定稿；`runtime` 含耗时
- [ ] `python scripts/selftest.py` 全 PASS；重复运行同一输入结果一致（确定性排序、无随机数）

## 9. 降级策略

- 无数据/空输入 → status=`insufficient_evidence`，退出码 0，写合法骨架并列缺口，不填充示例。
- 坏 JSON/坏编码 → 记 `errors`/`degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，`uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写 `limitations` 与 `pending_human`，不假装可下结论。
- 缺软件/缺依赖 → 如实标注 `limitations`；本 Skill 脚本只依赖标准库，XLSX/MAT/PDF 依赖平台工具，缺失时说明并用标准库兜底（PDF 文本不可得则仅记录无法解析）。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断写 `not_reported`/`pending_human`。

## 10. 边界

一个 Skill 只解决“实验结果解释与异常分析”这一类任务，不扩展为通用分析平台。没有真实材料时输出“材料不足”而非示例填充。result.json 是运行时产物，Skill 包内不得预置正式案例答案或白板。
