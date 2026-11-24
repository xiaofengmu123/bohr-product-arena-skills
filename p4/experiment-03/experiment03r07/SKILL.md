---
name: weekly-skill
description: >-
  实验参数优化建议 Skill（鲁棒性变体）：把固定研究目标与设备/材料/安全/成本约束转成一份可执行、可复核的参数方案，
  产出关键参数与可行区间、响应指标口径、搜索策略、成本时间预算、风险与异常处置，并输出带逐字溯源与不确定性的
  result.json（含 case_id、status、sources、uncertainty、pending_human、change_log）。强化空输入、坏 JSON、
  缺参数、超大文件等异常下的优雅降级，不崩溃、不编造。
version: 1.1.0
---

# 实验参数优化建议 Skill（鲁棒性变体）

## 1. 角色与何时使用

本 Skill 面向“有界参数优化（bounded parameter optimization）”任务：给定研究目标、设备材料、安全阈值、历史记录与预算约束，产出一份可直接执行并被独立复核的参数方案。适用：参数有有限定义域、有明确设备量程/材料耐温/安全阈值、有可测量响应指标。不适用：无任何材料与约束的开放式头脑风暴、纯文献综述、已确定最终工艺只需照抄 SOP 的情形。

核心目标（对应评分维度）：①建议参数落在真实可行域内（不越设备量程/材料耐温/安全阈值）；②每条权衡有明确响应指标与成本口径支撑；③候选点产生、每轮评估、取舍理由与停止判据可复核；④覆盖关键参数、响应指标、搜索策略、实验成本四项；⑤诚实标注依据、假设与需专家确认项，不编造数据或来源。

本变体在以上基础上强化鲁棒性：空输入、坏 JSON、坏编码、缺参数、超大文件、写盘失败等异常都要优雅降级、退出码可预期、不崩溃、不编造。这是与“通用方案包”的明确设计取舍：优先保证任何输入下都能留下合法、可诊断的产物，再逐步增强方案内容。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例标识 + 任务/约束元数据）+ `/input/task.md`（任务说明）+ 按 topic 提供的科研材料（PDF/文本）。
- 必须先“发现”再“假设”：扫描 `/input`，不假设固定文件名或键名；材料内容必须实际读取，PDF 用平台 `pdf-text-tool` 读取，不依据文件名/领域标签臆测，不假设存在预提取答案层。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例各独立运行。
- 脚本缺省参数：`--input /input --output /app/output --work /app/work`，均可覆盖用于本地测试。脚本只依赖 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段定义见 `references/output_contract.md`），至少包含：

- `case_id`（取自 `/input/case.json`，未知则如实标注为空并写 `pending_human`）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `plan`：主结果（假设、变量、对照、步骤、参数、资源、判读标准、响应指标、搜索策略、成本预算、依据、风险、异常处置、确认项）
- `artifacts`：可编辑产物 `plan.md` + `plan.json`，均真实写入 `/app/output/`
- `sources`：证据映射（每条含 locator + 逐字 quote）
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`

`status` 必须诚实：材料不足= `insufficient_evidence`，输入损坏= `input_error`，方案不完整= `partial`，全部满足且验证通过= `ok`。缺值给 `null`/`[]`，不改名、不删未知键。主产物 `plan.md` 可打开可编辑，`plan.json` 为结构化规格，二者与 `result.json` 路径互相对应且实际存在。

## 4. 执行流程（总 900 秒 / 64 请求；前 3 分钟先落盘骨架）

### 阶段 A（0–3 分钟，≤8 请求）——必须先落盘骨架

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
2. 读 `/app/work/input_digest.json`，并实际读取 `/input/case.json` + `task.md` + 材料文件。
3. 提取研究目标/变量/约束/响应指标第一版，写入 `/app/work/plan.json`（结构见 `references/output_contract.md`）。
4. `python scripts/validate_plan.py /app/work/plan.json --report /app/work/plan.validation_report.json`
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work` 写 `/app/output/result.json` 骨架：status=partial、case_id 已填、字段齐全、change_log 首条“骨架落盘”。**此步不得跳过**：超时/中断时仍可交付部分结果。

### 阶段 B（3–10 分钟，≤30 请求）——方案建模与约束校验

6. 逐条核定关键参数：名称/单位/建议值/可行区间/步进，每条附约束来源与逐字 quote。
7. 定义响应指标口径（单位、测量方法、方向、目标、可接受下限、不确定度）；说明用历史数据拟合/代理模型/经验规则给出预测，并披露预测不确定度。
8. 选定搜索策略（网格/序贯/贝叶斯/分阶段筛选），写明初始点、每轮补点、收敛与停止判据。
9. 每次修改后重跑 `validate_plan.py`，阻断错误必须清零；再跑 `python scripts/main.py doe --work /app/work --seed 0` 生成确定性候选点写入 `/app/work/candidates.json`。

### 阶段 C（10–14 分钟，≤20 请求）——成本、风险与自检

10. 补全成本/时间预算与多目标取舍；写全 risks / exception_handling / confirmations（安全、伦理、设备可用性、专家确认）。
11. 补全 sources（关键参数/响应/约束 → 材料位置 + 逐字 quote）与 uncertainty / limitations / pending_human。
12. `python scripts/check_result.py /app/output/result.json --output-dir /app/output`，逐条修复。

### 阶段 D（14–15 分钟，≤6 请求）——收尾

13. status 按实际取 ok/partial/insufficient_evidence/input_error；重跑 finalize + check_result；更新 change_log。

脚本调用一律用相对路径 `scripts/check_result.py`（相对 Skill 根 `/skills/weekly-skill`）；预算只作上限，不必用完。

## 5. 自检清单（逐条勾选）

- [ ] 已实际读取 /input/case.json + task.md + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，且全部落在设备量程/材料耐温/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote（locator + quote）
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点、每轮补点、收敛与停止判据；候选点可由 scripts/main.py doe 复现
- [ ] 实验成本与时间预算有明细口径，多目标取舍说明“牺牲多少可接受”
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处理覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认内容标注 pending_human
- [ ] result.json：case_id 正确、status 如实、sources/uncertainty/change_log 齐全、limitations 如实
- [ ] check_result.py 退出码 0 或仅警告（警告已说明）
- [ ] 鲁棒性：空输入/坏 JSON/缺参数/超大文件均无崩溃，errors/degradations 已记录
- [ ] 已跑 scripts/selftest.py 且退出码 0（提交前）

## 6. 降级策略（禁止编造）

- 材料不足：无法确定的参数/响应/约束写入 uncertainty 与 limitations，status=insufficient_evidence，不编造数值与来源。
- 越界冲突：目标温度与材料耐温冲突等写入 risks/exception_handling；validate_plan.py 阻断错误必须解决或明确降级（降低目标、换材料、转人工），不得悄悄改约束。
- 输入损坏：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读则 status=input_error。
- 缺软件：脚本只依赖标准库；plan.md 无需外部依赖。如需 PDF/PNG 预览而工具缺失，artifacts 只保留真实生成的格式并在 limitations 说明，不得伪造导出记录。
- 鲁棒性边界：空输入目录→ status=insufficient_evidence 且合法落盘；单文件 >2MB→跳过并记录 skipped:too_large；写盘失败→保留最小合法产物并记录 errors。
- 时间/请求不足：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. 脚本与参考

- `scripts/main.py`：init（发现输入+落骨架）/ finalize（合并 plan.json→result.json 并生成 plan.md、plan.json）/ doe（确定性候选点）。仅标准库。
- `scripts/validate_plan.py`：schema + 可行域约束校验，输出 errors/warnings；exit 0=通过、1=阻断、2=警告。
- `scripts/check_result.py`：校验 result.json 契约与产物文件存在性；exit 0=通过、1=阻断、2=警告。
- `scripts/selftest.py`：空输入、坏 JSON、缺参数、超大文件、最小合法流程自检；exit 0=全部通过。
- `references/output_contract.md`：plan.json 与 result.json 全部字段定义。
- `references/rules.md`：参数识别/响应口径/搜索策略/成本权衡/风险处置规则与鲁棒性清单。
