---
name: weekly-skill
description: >-
  实验参数优化建议 Skill：把固定研究目标、设备/材料/安全/成本约束与科研材料转成一份可执行、
  可复核的参数方案，产出关键参数与可行区间、响应指标口径、搜索策略、成本时间预算、风险与
  异常处置，并输出带逐字溯源与不确定性的 result.json（含 case_id、status、sources、
  uncertainty、change_log）。当需要把"研究问题 + 设备材料约束"落地为可开机参数方案时使用。
version: 1.0.0
---

# 实验参数优化建议 Skill

## 1. 角色与何时使用

面向**有界参数优化**：参数有有限定义域、有设备量程/材料耐温/安全阈值、有可测量响应指标。产出物是"可开机方案"，不是经验清单。不适用：无材料/约束的开放头脑风暴、纯文献综述、照抄既定 SOP 的复制任务。

核心目标（对应评分维度）：①建议参数落在真实可行域内（不越量程/耐温/安全阈值）；②每条权衡有明确响应指标与成本口径；③候选点产生、每轮评估、取舍理由与停止判据可复核；④关键参数、响应指标、搜索策略、实验成本四项全覆盖；⑤诚实标注依据、假设与需专家确认项，不编造数据或来源。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例标识 + 任务/约束元数据）、`/input/task.md`（任务说明）、按 topic 提供的科研材料（PDF/文本）。
- 必须实际读取材料内容，不依据文件名/领域标签臆测；PDF 用平台 `pdf-text-tool` 读取，不假设存在预提取答案层。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 result.json）。
- 预算：900 秒 / 64 次模型请求 / 单次响应含推理 ≤16384 token；3 个案例各独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段定义见 `references/output_contract.md`，至少含：

- `case_id`（取自 /input/case.json）；`status`：`ok | partial | insufficient_evidence | input_error`
- `plan`：主结果（假设、变量、对照、步骤、参数、资源、判读标准、响应指标、搜索策略、成本预算、依据、风险、异常处置、确认项）
- `artifacts`：可编辑产物 `plan.md` + `plan.json`，均真实写入 `/app/output/`
- `sources`：证据映射（每条 `locator` + 逐字 `quote`）
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤8 请求）——先落盘骨架（不得跳过）

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
2. 读 `/app/work/input_digest.json`，并实际读取 case.json + task.md + 材料文件。
3. 提取目标/变量/约束/响应指标第一版，写入 `/app/work/plan.json`。
4. `python scripts/main.py validate /app/work/plan.json --report /app/work/plan.validation_report.json`
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work`，得到 status=partial 的骨架 result.json。超时/中断仍可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——建模与约束校验

6. 逐条核定关键参数（名称/单位/建议值/可行区间/步进），每条附约束来源与逐字 quote。
7. 定义响应指标口径（单位/测量方法/方向/目标/可接受下限），披露预测方式与不确定度。
8. 选定搜索策略（网格/序贯/贝叶斯/分阶段），写明初始点、每轮补点、收敛与停止判据。
9. 每次修改重跑 validate；再 `python scripts/main.py doe --work /app/work --seed 0` 写 `/app/work/candidates.json`。

### 阶段 C：10–14 分钟（≤20 请求）——成本、风险与自检

10. 补全成本/时间预算与多目标取舍；写全 risks / exception_handling / confirmations。
11. 补全 sources（关键参数/响应/约束 → 材料位置 + 逐字 quote）与 uncertainty / limitations / pending_human。
12. `python scripts/main.py check /app/output/result.json --output-dir /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

13. status 按实际取 ok/partial/insufficient_evidence/input_error；重跑 finalize + check；更新 change_log。

脚本调用一律用相对路径（相对 Skill 根 `/skills/weekly-skill`）；预算只作上限，不必用完。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 已实际读取 case.json + task.md + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，全部落在设备量程/材料耐温/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote（locator + quote）
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点/每轮补点/收敛与停止判据，候选点可由 doe 复现
- [ ] 成本与时间预算有明细口径，多目标取舍说明"牺牲多少可接受"
- [ ] 假设/变量/对照/步骤/参数/资源/判读标准全覆盖
- [ ] 风险与异常处置覆盖：无可行解/约束冲突/数据缺失/设备报错/指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认标注 pending_human
- [ ] result.json：case_id 正确、status 如实、sources/uncertainty/change_log 齐全、limitations 如实
- [ ] check 退出码 0 或仅警告（警告已说明）

## 6. 降级策略（禁止编造）

- **材料不足**：无法确定的参数/响应/约束写入 uncertainty 与 limitations，status=insufficient_evidence，不编造数值与来源。
- **越界冲突**：目标温度与材料耐温冲突等写入 risks/exception_handling；validate 阻断错误必须解决或明确降级（降低目标/换材料/转人工），不得悄悄改约束。
- **输入损坏**：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读则 status=input_error。
- **缺软件**：脚本只依赖标准库；缺 PDF/PNG 预览则 artifacts 只保留真实生成的格式并在 limitations 说明，不伪造导出记录。
- **时间/请求不足**：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停，保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 7. 脚本与参考

- `scripts/main.py`：init / finalize / doe / validate / check / selftest 六子命令，单一编排器。
- `scripts/selftest.py`：空输入 / 坏 JSON / 正常输入三场景自检，退出码 0=通过；提交前运行 `python scripts/selftest.py`。
- `references/output_contract.md`：result.json / plan.json 全部字段定义 + 推理规则 + 完成标准。
- 脚本只用标准库，路径用相对路径，可离线运行。
