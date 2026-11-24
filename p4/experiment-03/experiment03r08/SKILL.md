---
name: weekly-skill
description: >-
  实验参数优化建议 Skill：把固定研究目标、设备/材料/安全阈值、组织方资源报价合同与版本化候选方案，
  转成一份可执行、可复核的参数方案。以"评分维度逐条对齐"为核心：16 个评分点（R01–R16）映射到
  工作流阶段与机检门槛，产出 result.json（case_id、status、plan、artifacts、sources、
  evidence_map、uncertainty、pending_human、change_log、rubric_trace、rubric_audit）
  与可编辑 plan.md。当任务是把科研材料转成"可开机的实验参数方案"时使用。
version: 2.0.0
---

# 实验参数优化建议 Skill

## 1. 角色与何时使用

面向**有界参数优化**（bounded parameter optimization）：参数有有限定义域、有设备量程/材料耐温/
安全阈值/成本合同、有可测量响应指标。输出不是解释，而是能拿去开机的方案：关键参数与可行区间、
响应指标口径、搜索策略、成本时间预算、风险与异常处置、需专家确认项。不适用：无材料无约束的开放
头脑风暴、纯文献综述、已定稿只需照抄的 SOP。

本版核心取舍：把题面 5 个评分维度、16 个评分点**逐条落到流程步骤与机检门槛**，并让
`result.json` 自带 `rubric_trace`（人工可读映射）与 `rubric_audit`（脚本计算的通过/警告/失败）。
这样"是否对齐评分维度"成为可核验事实，而不是自述。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例标识 + 任务/约束/合同/版本化候选方案元数据）、
  `/input/task.md`、按 topic 提供的科研材料（含 PDF）。
- **必须实际读取材料内容**，不依据文件名/领域标签臆测。PDF 用平台 `pdf-text-tool` 读取原文，
  不得假设存在预提取答案层；脚本只发现输入，不代读材料内容。
- 组织方输入为 concrete source-grounded 案例：真实科学来源、显式 prospective
  resource/price/booking 合同、每案 3 个版本化候选方案、有限参数域、真实算术与 required
  validation。这些不是虚构报价/安全审批/已测响应，方案不得写成"已验证事实"。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段见 `references/output_contract.md`，至少含：

- `case_id`（取自 `/input/case.json`）、`status`（`ok|partial|insufficient_evidence|input_error`）
- `plan`：主结果，覆盖假设、变量、对照、步骤、参数、资源、判读标准、响应指标、搜索策略、
  成本预算、依据、风险、异常处置、确认项
- `artifacts`：`plan.md` + `plan.json` + `audit_report.json`（均真实写入 `/app/output/`）
- `sources` / `evidence_map`：每条证据含 `locator` + 逐字 `quote`
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`
- `rubric_trace` / `rubric_audit`：16 个评分点的证据映射与机检结果（本版核心）

`plan.md` 可打开可编辑；`plan.json` 为结构化规格；二者与 `result.json` 路径对应且实际存在。

## 4. 评分维度对齐（16 个评分点 → 流程/门槛）

`references/rubric.md` 是权威表，`scripts/validate_plan.py` 内置同版本表计算 `rubric_audit`。
权重只标注来源，不用于自评打分。

| 评分点 | 维度/分值 | 落到哪个流程 | 机检门槛（gate） |
|-|-|-|-|
| R01 任务完成度 | 实际效果 15 | B：四项全覆盖 | parameters、response_metrics、search_strategy.method、cost_budget 均非空 |
| R02 结果正确性 | 实际效果 15 | B/C：可行域校验 | suggested_value ∈ feasible_range 且不越 equipment/material/safety 边界 |
| R03 结果完整性 | 实际效果 5 | B：方案全字段 | 四项 + assumptions/variables/controls/procedures 齐全 |
| R04 结果可核验性 | 实际效果 5 | B/C：确定性 + 溯源 | seed 数值；initial/per_round/stop_criteria；evidence_map.claims 非空 |
| R05 质量增益 | 使用增益 15 | C：证据溯源 | 有界域 ≥1；claims 非空；每参数有 locator+quote |
| R06 效率增益 | 使用增益 5 | B：搜索策略 | per_round 与 stop_criteria 非空（少返工） |
| R07 增益可验证性 | 使用增益 5 | C：公平对照 | 有 baseline 则记录；否则标注"缺基线，不自评" |
| R08 新增能力 | 创新增益 5 | B：约束感知 | feasible_range 有 min/max 且约束数 ≥1 |
| R09 方法改进 | 创新增益 5 | B：建模/搜索 | response_metrics 有 prediction_method；method ∈ grid/sequential/bayes/staged |
| R10 创新证据 | 创新增益 5 | B/C：收敛判据 | stop_criteria 非空且至少一条 prediction_note |
| R11 调用易用性 | 可靠体验 3 | A/D：流程可走通 | method、initial_points、procedures 非空 |
| R12 运行稳定性 | 可靠体验 4 | A/D：确定性/重跑 | seed 数值；errors 为空 |
| R13 异常处理 | 可靠体验 3 | C：五类异常 | exception_handling 覆盖无可行解/约束冲突/数据缺失/设备报错/指标矛盾 |
| R14 文档完整性 | 生态贡献 3 | D：确认项 | confirmations 覆盖安全/伦理/设备/专家四类 |
| R15 资源可复用性 | 生态贡献 4 | 交付前自检 | scripts 纯标准库；references 两个文件存在 |
| R16 分享推广价值 | 生态贡献 3 | D：产物齐备 | plan.json 与 change_log/limitations 如实 |

`status=ok` 要求硬门槛 R01/R02/R04/R13/R16 全过；否则保持 `partial` 或按证据取
`insufficient_evidence`/`input_error`。

## 5. 执行流程（总 900 秒 / 64 请求；预算只作上限）

脚本一律用相对 Skill 根 `/skills/weekly-skill` 的相对路径（如 `scripts/main.py`）。
**阶段 A 前 3 分钟必须落盘一版 partial 骨架，之后只做增量改进。**

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——骨架先行（R11/R12）

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
   → 写 `input_digest.json`、`plan.json` 骨架、`candidates.json` 占位，并立即写
   `/app/output/result.json`（`status=partial`、字段齐全、change_log 首条"骨架落盘"）。
   **此步不得跳过**：超时/中断仍可交付。
2. 读 `input_digest.json`；实际读取 `/input/case.json` + `task.md` + 材料/合同/候选方案文件。
3. 提取研究目标/变量/约束/响应指标第一版，回填 `plan.json`（结构见 output_contract.md）。
4. `python scripts/validate_plan.py /app/work/plan.json --report /app/work/plan.validation_report.json`
   （exit 0/1/2；骨架阶段允许 exit 2 警告）。
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work`
   重写骨架与 plan.md；更新 change_log。

### 阶段 B：3–10 分钟（≤30 请求）——可行域、指标与搜索（R01/R03/R04/R06/R08/R09/R10）

6. 逐条核定关键参数：名称/单位/建议值/可行区间/步进；硬边界取设备量程 ∩ 材料耐温 ∩ 安全阈值 ∩
   组织方合同约束；每条附 `constraint_source` + `locator` + 逐字 `quote`。
7. 定义响应指标口径（单位/测量方法/方向/目标/可接受下限/不确定度）；写明预测方式
   （历史拟合/代理模型/经验规则）并披露预测不确定度。
8. 选定搜索策略（grid/sequential/bayes/staged），写明初始点、每轮补点、如何用上一轮结果调整、
   收敛与停止判据。对 3 个版本化候选方案：逐一校验其参数域与算术（成本=数量×单价，时间可加总），
   逐条记 `validation` 结论，选出可行且成本-响应最优者作为建议基准。
9. 每次修改后重跑 `validate_plan.py`，阻断错误清零；`python scripts/main.py doe --work /app/work --seed 0`
   确定性生成候选点（固定 seed 复现同一组点）。

### 阶段 C：10–14 分钟（≤20 请求）——成本、风险、溯源（R02/R05/R07/R13）

10. 补全成本/时间明细与多目标取舍（tradeoff_note/acceptance_criteria：成本受限时牺牲多少响应可接受）；
    写全 risks/exception_handling（五类）/confirmations（安全、伦理、设备可用性、专家确认）。
11. 补全 `sources` + `evidence_map`（每条关键参数/响应/约束 → 材料位置 + 逐字 quote）与
    `uncertainty`/`limitations`/`pending_human`；来源冲突双方都记，不隐藏。
12. `python scripts/main.py finalize ...`；`python scripts/validate_plan.py /app/output/result.json --report ...`
    逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾自检（R14/R15/R16）

13. `status` 按实际取四枚举之一；重跑 `finalize` + `validate_plan.py`；更新 change_log。
14. `python scripts/selftest.py`（交付前自检，exit 0 证明脚本链路可用）。

## 6. 自检清单（逐条对应评分点）

- [ ] R11/A：已实际读取 case.json + task.md + 材料内容（非仅文件名/标签）
- [ ] R01/B：关键参数、响应指标、搜索策略、成本预算四项均非空
- [ ] R02/R08/B：每参数含名称/单位/建议值/可行区间/步进，值落在设备/材料/安全/合同边界交集内
- [ ] R05/C：每条参数有 constraint_source + locator + 逐字 quote；冲突标 source_conflict
- [ ] R09/R10/B：响应指标含口径/单位/测量法/方向/目标/下限/预测方式/预测披露
- [ ] R04/R06/B：搜索策略含方法/初始点/每轮补点/停止判据；候选点由 `scripts/main.py doe` 复现
- [ ] R13/C：异常处置覆盖无可行解、约束冲突、数据缺失、设备报错、指标矛盾五类
- [ ] R14/D：确认项覆盖安全/伦理/设备可用性/专家确认；未确认项写入 pending_human
- [ ] R03/B：assumptions/variables/controls/procedures/resources/interpretation_criteria 齐全
- [ ] R07/C：有 baseline 则记录，否则在 rubric_trace.notes 标"缺基线，不自评"
- [ ] R12/A：init 先落盘 partial 骨架，超时/中断仍可交付
- [ ] R16/D：result.json 字段齐全、status 如实、artifacts 存在、change_log/limitations 如实
- [ ] R15/D：`python scripts/validate_plan.py /app/output/result.json` exit 0 或仅警告；`python scripts/selftest.py` exit 0

## 7. 降级策略（禁止编造）

- **材料不足**：无法确定的参数/响应/约束写入 uncertainty/limitations，`status=insufficient_evidence`，不编数值与来源。
- **越界/约束冲突**：目标温度 vs 材料耐温等写入 risks/exception_handling；validate 阻断错误必须解决或明确降级（降低目标/换材料/转人工），不得悄悄改约束。
- **无可行解**：可行域交集为空时 status 降级，exception_handling 给终止/转人工判断。
- **输入损坏**：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读则 status=input_error。
- **缺软件**：脚本只依赖标准库；plan.md 无需外部依赖。PDF/PNG 预览工具缺失时 artifacts 只保留真实生成的格式并在 limitations 说明，不伪造导出记录。
- **时间/请求不足**：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 8. 脚本与参考

- `scripts/main.py`：`init`（发现输入+骨架并立即 finalize）/ `finalize`（plan.json→result.json+plan.md）/ `doe`（确定性候选点）/ `audit`（重算 rubric_audit）。纯标准库。
- `scripts/validate_plan.py`：schema + 可行域 + 16 评分点机检；exit 0=通过、1=阻断、2=警告。
- `scripts/selftest.py`：合成案例全链路自检（含空输入/坏 JSON/越界三路降级），exit 0=通过。
- `references/output_contract.md`：plan.json 与 result.json 全部字段定义。
- `references/rubric.md`：16 评分点映射表、门槛与状态语义。
