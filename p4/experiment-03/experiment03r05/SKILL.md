---
name: weekly-skill
description: 有界实验参数优化 Skill：把固定研究目标与设备量程/材料耐温/安全阈值/成本预算约束转成一份可执行、可复核的参数方案，产出关键参数（名称/单位/建议值/可行区间/步进）、响应指标口径、搜索策略与停止判据、成本时间预算、风险与异常处置，并写出带逐字溯源、不确定性与 change_log 的 /app/output/result.json。
version: 2.0.0
---

# 有界实验参数优化 Skill

## 1. 角色与何时使用

把「给定研究目标 + 设备/材料/安全/成本约束 + 历史材料」转成一份能拿去开机的实验参数方案。适用于**有界参数优化**：参数有有限定义域、设备量程与材料耐温/安全阈值可查到、响应可测量、成本可计量。核心目标对齐评分维度：①建议值落在真实可行域内；②每条权衡有响应指标与成本口径支撑；③候选点产生、每轮评估、取舍理由与停止判据可复核；④四项全覆盖（关键参数/响应指标/搜索策略/实验成本）；⑤诚实标注依据、假设与待确认项，不编造。

不适用：无任何材料/约束的开放式头脑风暴、纯文献综述、已有最终工艺只需照抄 SOP、无设备材料信息的情形。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（case_id + 任务/约束元数据，可能含 `output_contract`）+ `/input/task.md` + 按 topic 提供的科研材料/PDF。
- **必须实际读取文件内容**，不依据文件名/领域标签臆测；PDF 用平台 `pdf-text-tool` 读取，不假设存在预提取答案层。
- 若 `/input/case.json` 含 `output_contract`，其全部键必须原样回填进 result.json（不改名、不删未知键、缺值给 `null`/`[]`）。
- 工作目录 `/app/work`；最终产物写入 `/app/output/`（含 `result.json`）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段定义见 `references/output_contract.md`），至少包含：

- `case_id`（取自 `/input/case.json`）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `plan`：主结果（假设/变量/对照/步骤/参数/资源/判读标准/响应指标/搜索策略/成本预算/依据/风险/异常处置/确认项）
- `artifacts`：可编辑产物 `plan.md` + `plan.json`，均真实写入 `/app/output/`
- `sources`：证据映射（每条含 `locator` + 逐字 `quote`，并标注支持哪些字段）
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`

`change_log` 是必填审计轨迹：每次修改追加一条 `{time, step, action, detail, content_hash}`，首条必须为「骨架落盘」。`plan.json` 与 `result.json` 的 `plan` 一致且路径互相对应。参数每条须含 `名称/单位/建议值/可行区间(min≤max)/步进(>0)/约束来源/逐字 quote`；响应指标每条须含 `口径/单位/测量方法/方向/目标/可接受下限/预测方式/不确定度`。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架
1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
2. 读 `/app/work/input_digest.json`，实际读取 `/input/case.json` + `task.md` + 全部材料。
3. 提取研究目标/变量/约束/响应指标第一版，写入 `/app/work/plan.json`。
4. `python scripts/validate.py plan --plan /app/work/plan.json --report /app/work/plan.validation_report.json`
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work` 写 `result.json` 骨架：status=partial、case_id 已填、字段齐全、change_log 首条「骨架落盘」。**此步不得跳过**：超时/中断仍可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——方案建模与约束校验
6. 逐条核定关键参数：名称/单位/建议值/可行区间/步进，每条附约束来源与逐字 quote；建议值必须落在可行区间内。
7. 定义响应指标口径（单位/测量方法/方向/目标/可接受下限/不确定度），披露预测方式（历史拟合/代理模型/经验规则）与预测不确定度。
8. 选定搜索策略（网格/序贯/贝叶斯/分阶段筛选），写清初始点、每轮补点、收敛与停止判据。
9. 每次修改后重跑 validate.py，阻断错误必须清零；再 `python scripts/main.py doe --work /app/work --seed 0` 生成确定性候选点 → `/app/work/candidates.json`。

### 阶段 C：10–14 分钟（≤20 请求）——成本、风险与自检
10. 补全成本/时间预算与多目标取舍；写全 risks / exception_handling / confirmations（安全、伦理、设备可用性、专家确认）。
11. 补全 sources（关键参数/响应/约束 → 材料位置 + 逐字 quote）与 uncertainty / limitations / pending_human。
12. `python scripts/validate.py result --result /app/output/result.json --output-dir /app/output`，逐条修复阻断错误。

### 阶段 D：14–15 分钟（≤6 请求）——收尾
13. status 按实际取 ok/partial/insufficient_evidence/input_error；重跑 finalize + validate；更新 change_log。

脚本调用一律用相对路径（相对 Skill 根 `/skills/weekly-skill`）；预算只作上限，不必用完。相同输入 + 相同 seed 必须得到相同候选点与相同 result（固定 seed、规范化 JSON、change_log 记录 content_hash）。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已实际读取 `/input/case.json` + task.md + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，且全部落在设备量程/材料耐温/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote（locator + quote）
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点、每轮补点、收敛与停止判据；候选点可由 `main.py doe` 复现
- [ ] 实验成本与时间预算有明细口径，多目标取舍说明「牺牲多少可接受」
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处置覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认内容标注 pending_human
- [ ] result.json：case_id 正确、status 如实、sources/uncertainty/change_log 齐全、limitations 如实
- [ ] `python scripts/selftest.py` 退出码 0；`validate.py result` 退出码 0 或仅警告（警告已说明）

## 6. 降级策略（禁止编造）

- 材料不足：无法确定的参数/响应/约束写入 uncertainty 与 limitations，status=insufficient_evidence，不编造数值与来源。
- 越界冲突：目标温度与材料耐温冲突等写入 risks/exception_handling；validate.py 阻断错误必须解决或明确降级（降目标/换材料/转人工），不得悄悄改约束。
- 输入损坏：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读则 status=input_error。
- 缺软件：脚本只依赖标准库；plan.md 无需外部依赖。需 PDF/PNG 预览而工具缺失时，artifacts 只保留真实生成的格式并在 limitations 说明，不伪造导出记录。
- 时间/请求不足：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. 脚本与参考

- `scripts/main.py`：init（发现输入+落盘骨架）/ finalize（合并 plan.json→result.json 并渲染 plan.md）/ doe（确定性候选点）。
- `scripts/validate.py`：plan 与 result 两级校验（schema + 可行域 + 产物存在性），exit 0=通过、1=阻断、2=警告。
- `scripts/selftest.py`：合成输入自检（happy path / 空输入 / 坏 JSON / 约束冲突），不含正式答案。
- `references/output_contract.md`：result.json 与 plan.json 全部字段定义。
- `references/rules.md`：参数识别/响应口径/搜索策略/成本权衡/风险处置规则与清单。
