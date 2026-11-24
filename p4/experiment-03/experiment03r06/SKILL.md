---
name: weekly-skill
description: >-
  实验参数优化建议 Skill：把固定研究目标与设备/材料/安全/成本约束转成一份可执行、可复核的参数方案。
  通过分层结构化中间表示（constraint_model / response_model / search_model / evidence_map / tradeoff_model）
  与逐字证据溯源，产出 result.json（含 case_id、status、sources、uncertainty、pending_human、change_log）
  及可编辑 plan.md。当任务是把科研材料转成“可开机的实验参数方案”时使用。
version: 1.1.0
---

# 实验参数优化建议 Skill

## 1. 角色与何时使用

本 Skill 面向“实验参数优化建议”：给定研究目标、设备/材料/安全阈值、历史记录与预算约束，产出一份可直接执行并被独立复核的参数方案。适用场景为**有界参数优化**（参数有有限定义域、有明确设备量程/材料耐温/安全阈值、有可测量响应指标）。不适用：无材料无约束的开放式头脑风暴、纯文献综述、已定稿只需照抄的 SOP。

核心目标（对应评分维度）：①建议参数落在真实可行域内（不越设备量程/材料耐温/安全阈值）；②每条权衡有明确响应指标与成本口径支撑；③候选点产生、每轮评估、取舍理由与停止判据可复核；④四项全覆盖（关键参数、响应指标、搜索策略、实验成本）；⑤诚实标注依据、假设与待确认项，不编造数据或来源。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例标识 + 任务/约束元数据）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料/PDF。
- **必须实际读取材料文件内容**，不依据文件名/领域标签臆测；PDF 用平台 `pdf-text-tool` 读取，不要假设存在预提取答案层。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段定义见 `references/output_contract.md`），至少包含：

- `case_id`（取自 `/input/case.json`）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `plan`：主结果，覆盖假设、变量、对照、步骤、参数、资源、判读标准、响应指标、搜索策略、成本预算、依据、风险、异常处置、确认项
- `artifacts`：可编辑产物（`plan.md` + `plan.json`，均真实写入 `/app/output/`）
- `sources`：证据映射（每条含 locator + 逐字 quote）
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`
- `intermediate_representations`：结构化中间产物清单与溯源链（本版核心，见 §4）

主产物 `plan.md` 可打开可编辑，`plan.json` 为结构化规格，二者与 `result.json` 路径互相对应且实际存在。

## 4. 中间表示与可复核（本版差异化设计）

本版把“一段建议”拆成五层**结构化中间表示**，每层独立可校验、可追溯、可增量修改；`result.json` 只做组装，不另起炉灶。全部落在 `/app/work/`：

| 文件 | 内容 | 关键字段 |
|---|---|---|
| `input_digest.json` | 输入发现结果 | files、case_id、preview、可读性 |
| `constraint_model.json` | 可行域模型 | 每参数 domain/hard_bounds/constraints/status、feasibility |
| `response_model.json` | 响应指标与预测模型 | metrics（口径/单位/方向/目标/下限/测量法）、relations、uncertainty |
| `search_model.json` | 搜索策略与候选点 | method、initial_points、stop_criteria、seed、candidates |
| `evidence_map.json` | 证据映射 | 每条 source 的 locator + 逐字 quote + claims |
| `tradeoff_model.json` | 成本/时间/多目标取舍 | cost_items、total、time_hours、acceptance_criteria |
| `trace.json` | 溯源链/变更记录 | 每步 stage、action、受影响文件 |

**可复核原则**：每个关键数值/约束/指标都要能指回材料逐字片段（locator + quote）；`evidence_map.json` 是全链证据根，`plan.json` 的 `sources` 从其组装。所有中间表示含 `schema_version` 与 `case_id`，可被 `scripts/validate_plan.py` 单独校验；`scripts/main.py doe` 从 `search_model.json` 确定性生成候选点，固定 seed 复现同一组点。

## 5. 执行流程（总 900 秒 / 64 请求）

脚本一律用相对 Skill 根 `/skills/weekly-skill` 的相对路径调用（如 `scripts/main.py`）；预算只作上限，不必用完。任何一步失败都要保留已落盘的部分结果。

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`：写 `/app/work/input_digest.json`、五层中间表示骨架、`plan.json` 骨架，并写 `/app/output/result.json` 骨架（`status=partial`、`case_id` 已填、字段齐全、change_log 首条“骨架落盘”）。**此步不得跳过**。
2. 读 `input_digest.json`，并**实际读取** `/input/case.json` + `task.md` + 材料文件。
3. 提取研究目标/变量/约束/响应指标第一版，逐层回填五个中间表示。
4. `python scripts/validate_plan.py /app/work/plan.json --report /app/work/plan.validation_report.json`。
5. `python scripts/main.py trace --work /app/work --stage A --message "..."` 记录变更；`python scripts/main.py finalize ...` 重写骨架。超时/中断时仍可交付。

### 阶段 B：3–10 分钟（≤30 请求）——建模与约束校验

6. 逐条核定关键参数：名称/单位/建议值/可行区间/步进，每条附约束来源与逐字 quote（写入 `constraint_model.json`）。
7. 定义响应指标口径（单位、测量方法、方向、目标、可接受下限、不确定度）；说明用历史拟合/代理模型/经验规则给出预测并披露预测不确定度（写入 `response_model.json`）。
8. 选定搜索策略（网格/序贯/贝叶斯/分阶段筛选），写明初始点、每轮补点、收敛与停止判据（写入 `search_model.json`）。
9. 每次修改后重跑 `validate_plan.py`，阻断错误清零；再跑 `python scripts/main.py doe --work /app/work --seed 0` 生成确定性候选点写入 `search_model.json` 的 `candidates` 并落盘 `candidates.json`。

### 阶段 C：10–14 分钟（≤20 请求）——成本、风险与自检

10. 补全成本/时间预算与多目标取舍（`tradeoff_model.json`）；写全 `risks` / `exception_handling` / `confirmations`（安全、伦理、设备可用性、专家确认）。
11. 补全 `evidence_map.json`（关键参数/响应/约束 → 材料位置 + 逐字 quote）与 `uncertainty` / `limitations` / `pending_human`。
12. `python scripts/main.py finalize ...` 组装 `result.json`；`python scripts/validate_plan.py /app/output/result.json --report ...`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

13. `status` 按实际取 `ok/partial/insufficient_evidence/input_error`；重跑 `finalize` + `validate_plan.py`；更新 `change_log` 与 `trace.json`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 已实际读取 `/input/case.json` + `task.md` + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，且全部落在设备量程/材料耐温/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote（locator + quote），冲突已标 `source_conflict`
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点、每轮补点、收敛与停止判据；候选点可由 `scripts/main.py doe` 复现
- [ ] 实验成本与时间预算有明细口径，多目标取舍说明“牺牲多少可接受”
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处置覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认内容标注 `pending_human`
- [ ] 五层中间表示均已回填且通过 `validate_plan.py`（exit 0 或仅警告）
- [ ] `result.json`：`case_id` 正确、`status` 如实、`sources/uncertainty/change_log` 齐全、`limitations` 如实
- [ ] 可运行 `python scripts/selftest.py`，退出码 0 证明脚本链路可用

## 7. 降级策略（禁止编造）

- **材料不足**：无法确定的参数/响应/约束写入 `uncertainty` 与 `limitations`，`status=insufficient_evidence`，不编造数值与来源。
- **越界冲突**：目标温度与材料耐温冲突等写入 `risks/exception_handling`；`validate_plan.py` 阻断错误必须解决或明确降级（降低目标、换材料、转人工），不得悄悄改约束。
- **输入损坏**：坏 JSON/坏编码捕获到 `errors/degradations`，继续处理其余材料；全部不可读则 `status=input_error`。
- **缺软件**：脚本只依赖标准库；`plan.md` 无需外部依赖。如需 PDF/PNG 预览而工具缺失，`artifacts` 只保留真实生成的格式并在 `limitations` 说明，不得伪造导出记录。
- **时间/请求不足**：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停止并保留当前 `result.json`。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 8. 脚本与参考

- `scripts/main.py`：`init`（发现输入+五层骨架）/ `finalize`（组装 plan.json→result.json 并生成 plan.md）/ `doe`（确定性候选点）/ `trace`（溯源记录）。
- `scripts/validate_plan.py`：校验 plan.json、result.json 与各中间表示的 schema + 可行域 + 证据完整性；输出 errors/warnings；exit 0=通过、1=阻断、2=警告。
- `scripts/selftest.py`：端到端自检（临时目录跑 init→doe→finalize→validate，含空输入降级），exit 0=通过。
- `references/output_contract.md`：plan.json、result.json 与中间表示的全部字段定义。
- `references/rules.md`：参数识别/响应口径/搜索策略/成本权衡/风险处置规则与检查清单。
