---
name: experiment-result-interpretation
description: 科研实验结果解释与异常分析 Skill：把实验读数、时序/弯曲曲线、多批次多通道数据、计算实验指标与设备日志，转化为趋势、异常点、可能原因、复核步骤、后续实验建议五段可复核结论；同时给出假设/变量/对照/参数/资源/判读标准与风险、确认项。输出结构化 result.json 与可编辑 report.md，支持异常定位、按可检验性给原因排序、生成可执行复核清单与证据溯源。当需要解读实验数据、判定异常或规划复核/补做实验时使用。
version: 1.1.0
---

# 实验结果解释与异常分析

## 1. 角色与何时使用

本 Skill 面向“一轮实验跑完后”的解释工作：把原始读数、时序/弯曲曲线、多批次多通道
数据、图谱、计算实验指标与设备日志，转化为五段可检查结论——趋势、异常点、可能原因、
复核步骤、后续实验建议，并补全实验方案（假设/变量/对照/参数/判读标准）与风险/确认项。
适用于有数据文件（CSV/TSV/JSON/表格/曲线/多 sheet 工作簿/PDF 论文）且需回答“数据说明
什么趋势、哪个点异常、可能为何、下一步复核什么、补做什么实验”的场景。

不适用于：无任何数据文件的纯理论问答、需合规签字的临床与安全结论、从零设计全新实验方案
（本 Skill 只把方案作为解释依据补全）。没有真实材料时输出“材料不足”，不填充示例答案。

## 2. 输入契约

- 运行时输入固定位于 `/input/`：`/input/case.json`（案例元数据与约束，可能含
  `case_id/id/challenge_id`、`output_contract`、材料清单）、`/input/task.md`（任务与
  评分要求）。材料文件在 `/input/` 任意子目录（`materials/`、`data/`、PDF、CSV/XLSX/
  MAT 等）。必须实际读取文件内容，不得按文件名或标签臆断来源。
- 平台三个固定案例的典型形态（只按实际读到的内容处理，不预设答案）：① 多根原始弯曲
  曲线 + 原始样本工作簿/MAT；② 五折/双估计器的 Iris 计算实验；③ 12 个 sheet 的
  Cefixime 工作簿 + 完整论文。曲线、多 sheet、计算指标都要定位到具体文件/列/行/通道。
- 文本按 UTF-8（含 BOM）读取，坏编码降级 latin-1 并记 `degradations`；坏 JSON 记
  `errors` 后继续处理其余材料；单文件 >2MB 跳过并记录。
- XLSX/MAT/PDF 由 Agent 先用平台工具（pandas/openpyxl/scipy/pdftotext）转成 CSV 或纯
  文本到 `/app/work/`，再交给脚本画像与阅读；转换命令属于 harness 工具链，不是本 Skill
  脚本依赖。工作目录 `/app/work/`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`（可编辑），可选
`evidence_map.json`、`data_profile.json`、`figure_*.svg`。`result.json` 顶层字段：

`case_id`、`status`（`ok|partial|insufficient_evidence|input_error`）、`task_type`、
`summary`、`interpretation`（`trends/anomalies/possible_causes/verification_steps/
followup_experiments`）、`experiment_plan`（`hypothesis/testable_question/variables/
controls/steps/parameters/resources/interpretation_criteria`）、`evidence_and_risk`
（`evidence_basis/risk_and_anomaly_handling/assumptions`）、`confirmation_items`
（`safety/ethics/equipment_availability/expert_confirmation`）、`sources`、`uncertainty`、
`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`。

字段精确定义见 `references/output_contract.md`；未知值写 `null` 或 `[]`，不改名不删键。
若 `case.json` 提供 `output_contract`，必须原样回填其全部字段。每条关键结论必须带
`evidence` 数组，每项含 `locator`（如 `materials/paper.md#p3`、`data/batch_12.csv#L34`）
与 `quote`（逐字摘录）。`status` 必须诚实：材料不足写 `insufficient_evidence`，绝不
编造数据、机理或重复数。

## 4. 执行流程（总 900 秒、64 次模型请求）

时间与请求按阶段硬性拆分，**前 3 分钟内必须先落盘一版合法 partial 骨架，之后只做
增量改进**；任何中断都留下可读 partial 结果，不允许为追求完美而延误落盘。

| 阶段 | 累计时限 | 请求上限 | 输入 → 输出 |
|-|-|-|-|
| P0 骨架 | 0–180s | ≤6 | 读 case.json+task.md → `skeleton` 写出合法 partial `result.json`+`report.md`+`run_state.json` |
| P1 画像 | 180–480s | ≤18 | 扫描材料、转格式 → `inspect` 写 `data_profile.json`（文件清单/缺失/均值/中位数/标准差/z-score/IQR/MAD 离群/饱和/坏编码），定位每列到文件/行 |
| P2 结论 | 480–780s | ≤28 | 依画像与原文逐条写五段结论+方案+风险+确认项，每条绑定 locator+quote，**每写一段就增量写回 result.json** |
| P3 定稿 | 780–900s | ≤8 | `validate` 校验 schema/证据 → 补 `uncertainty/limitations/pending_human/change_log` → `finalize` 写 runtime 并渲染 report.md |

另预留 4 次请求作缓冲（64−6−18−28−8=4），不用于无依据扩展。每完成一个子步骤先写回
result.json 再决定是否继续；任一阶段逼近时限立即 `finalize` 用已有内容闭环，宁可
`partial` 也不交不完整半成品。请求优先用于“读原文→提取事实→核对证据”，不用于臆测。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python scripts/main.py skeleton --input /input --output /app/output --work /app/work
python scripts/main.py inspect   --input /input --output /app/output --work /app/work
python scripts/main.py validate  --input /app/output/result.json
python scripts/main.py finalize  --input /app/output/result.json --work /app/work
python scripts/main.py checkpoint --phase P2 --requests-used 20 --work /app/work
```

缺省 `--input/--output/--work` 为 `/input`、`/app/output`、`/app/work`。脚本确定性执行
（固定排序、无随机数），失败退出码非 0 并在 stderr 说明；Agent 不得因脚本失败而编造
结果。自检：`python scripts/selftest.py`（在临时目录构造合成输入跑完整流水线并断言）。

## 6. 自检清单（定稿前逐条勾选）

- [ ] `result.json` 可被 `json.load` 解析，`case_id` 非空，`status` 合法
- [ ] 顶层键齐全；`case.json` 的 `output_contract` 全部字段已回填、未改名未删键
- [ ] 五段 `interpretation` 全部非空或如实写空并在 `limitations` 标注原因
- [ ] 每条结论带 evidence（locator+quote），可回指具体文件/字段/行
- [ ] 异常点写明阈值方法+数值；原因写明支持/排除证据、`confound_risk` 与可检验性
- [ ] 复核步骤为可执行动作（含 target/判据），无“检查数据质量”类空话
- [ ] 单位、方法、阈值歧义被保留并写入 `uncertainty`
- [ ] 缺失材料、坏编码、超量程/饱和、对照缺失、重复数不足被识别并写入
      `limitations/degradations/errors`
- [ ] `experiment_plan/evidence_and_risk/confirmation_items` 已填；未经专家确认的事项
      进 `pending_human`，不写成事实
- [ ] `change_log` 记录骨架→画像→结论→定稿各阶段；`runtime` 含耗时与请求数
- [ ] 重复运行同一输入结果一致（`python scripts/selftest.py` 通过）

## 7. 降级策略

- 无数据/空输入 → `status=insufficient_evidence`，退出码 0，写合法骨架并列出缺口，不填充示例。
- 坏 JSON/坏编码 → 记 `errors/degradations`，继续处理其余材料。
- 单位不一致/读数超量程或饱和 → 保留原始单位，在 `uncertainty` 标注，不做无依据换算或插补。
- 对照缺失/重复数不足 → 结论降置信，写入 `limitations` 与 `pending_human`。
- 缺软件/缺依赖 → 如实标 `limitations`；本 Skill 脚本只依赖标准库，平台工具缺失时说明并用
  标准库兜底（如 pdftotext 缺失则只处理已转出的文本，标记 PDF 未解析）。
- 严禁编造评测分数、机理、重复数或运行结果；无法判断就写 `not_reported/pending_human`。

## 8. 边界

一个 Skill 只解决“实验结果解释与异常分析”这一类任务，不扩展为通用分析平台。交付的
`result.json` 是运行时产物，Skill 包内不得预置正式案例答案或白板。示意输出必须标注
“示例”，不冒充正式案例结果。
