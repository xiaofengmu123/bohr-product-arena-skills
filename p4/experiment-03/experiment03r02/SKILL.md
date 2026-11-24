---
name: weekly-skill
description: >-
  实验参数优化建议 Skill：把固定研究目标、设备/材料/安全约束与组织方提供的 3 版候选方案，
  转成可执行、可复核的参数方案。对候选点做可行性判定与成本-响应联合权衡，产出关键参数与
  可行区间、响应指标口径、搜索策略、成本时间预算、风险与异常处置，并输出带逐字溯源与
  不确定性的 result.json（case_id、status、sources、uncertainty、change_log）。当任务是把
  科研材料与约束转成"可开机的实验参数方案"时使用。
version: 1.1.0
---

# 实验参数优化建议 Skill（bounded parameter optimization）

## 1. 角色与何时使用

本 Skill 面向"有界参数优化建议"：给定研究目标、设备量程、材料耐温、安全阈值、历史记录、预算与组织方提供的版本化候选方案，产出一份可直接执行并被独立复核的参数方案。判断做对只看三条：建议参数落在真实可行域内；每条权衡有明确响应指标与成本口径支撑；候选点产生、每轮评估、取舍理由与停止判据能被他人复跑核对。

适用：参数有有限定义域、有明确设备/材料/安全阈值、有可测量响应指标、有 3 版可比较候选方案或需要从零生成候选点。不适用：开放式头脑风暴、纯文献综述、已定稿只需照抄 SOP 的情形。

## 2. 输入契约

- 运行时原始输入：`/input/case.json`（案例标识 + 约束/预算元数据）、`/input/task.md`、按 topic 提供的科研材料/PDF。组织方案例常含 **3 版候选方案**（plans.json / candidate_plans / plans 数组）、有限参数定义域、显式资源/价格/预约合同。
- 必须实际读取材料文件内容，不依据文件名或领域标签臆测；PDF 用平台 `pdf-text-tool` 读取，不假设存在预提取答案层。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 result.json）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例各独立运行。预算只作上限，不必用完。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段定义见 `references/output_contract.md`），至少包含：

- `case_id`：取自 `/input/case.json`，缺省用文件名兜底。
- `status`：`ok | partial | insufficient_evidence | input_error`，如实标注。
- `plan`：主结果，覆盖假设、变量、对照、步骤、参数、资源、判读标准、响应指标、搜索策略、成本预算、依据、风险、异常处置、确认项。
- `candidates` + `candidate_evaluation`：若输入提供 3 版候选方案，逐版记录来源、参数、可行性判定、成本算术与 Pareto 排序。
- `artifacts`：可编辑产物 `plan.md` + `plan.json`，均真实写入 `/app/output/` 且路径互相对应。
- `sources`：证据映射，每条含 `locator` + 逐字 `quote`。
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`。

## 4. 执行流程（900 秒 / 64 请求，分阶段预算）

核心原则：**前 3 分钟先落盘合法 partial 骨架，之后只做增量改进**。任何一步中断都能交付可读的部分结果。

### 阶段 0：0–3 分钟（≤6 请求）——骨架必须落盘

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
2. 读 `/app/work/input_digest.json`，实际读取 `/input/case.json` + `task.md` + 材料文件。
3. 提取研究目标/变量/约束/响应指标/候选方案第一版，写入 `/app/work/plan.json`（结构见 `references/output_contract.md`）。
4. `python scripts/validate_plan.py /app/work/plan.json --report /app/work/plan.validation_report.json`
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work` 写 `/app/output/result.json` 骨架：status=partial、case_id 已填、字段齐全、change_log 首条"骨架落盘"。**此步不得跳过**。

### 阶段 1：3–8 分钟（≤24 请求）——参数、约束与候选方案评估

6. 逐条核定关键参数：名称/单位/建议值/可行区间/步进，每条附约束来源与逐字 quote；建议值必须落在有限定义域内。
7. 若存在 3 版候选方案，逐版执行可行性判定与成本算术：`python scripts/main.py evaluate --work /app/work --seed 0`，记录违规项、总成本、是否超预算、预测响应与 Pareto 排序。
8. 定义响应指标口径（单位/测量方法/方向/目标/可接受下限）；披露预测方式（历史拟合/代理模型/经验规则）与预测不确定度。
9. 选定搜索策略（网格/序贯/贝叶斯/分阶段筛选），写明初始点、每轮补点、收敛与停止判据；候选点可由 `scripts/main.py doe` 确定性复现。

### 阶段 2：8–13 分钟（≤24 请求）——成本、风险与自检

10. 补全成本/时间预算与多目标取舍，写全 risks / exception_handling / confirmations。
11. 补全 sources / uncertainty / limitations / pending_human。
12. 每次修改后重跑 `scripts/validate_plan.py`，阻断错误清零；再跑 `scripts/main.py doe`。

### 阶段 3：13–15 分钟（≤10 请求）——收尾

13. status 按实际取 ok/partial/insufficient_evidence/input_error；重跑 `scripts/main.py finalize` 与 `scripts/main.py check --output-dir /app/output`；更新 change_log。

脚本调用一律用相对路径（相对 Skill 根 `/skills/weekly-skill`）。

## 5. 版本化候选方案的处理（本版重点）

- 输入含 3 版候选方案时，**不默认任何一版正确**：逐版抽取参数值、成本项、响应预测，与设备量程/材料耐温/安全阈值逐项比对。
- 可行性判定为**硬门槛**：任一参数越界或成本超预算 → 该版判为不可行并记录具体违规，不进入最优推荐。
- 可行版本之间按成本-响应联合权衡排序：写明"为把某项响应从 A 提到 B，需要多花多少成本/时间"，并给出可接受的牺牲上限。
- 候选方案是规划场景（prospective），**不得**声称为实际实验室报价、供应商报价、安全审批或实测未来响应；所有价格/产能/响应预测均标注为组织方给定或模型估计。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 已实际读取 case.json + task.md + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，全部落在有限定义域与设备/材料/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote（locator + quote）
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点/每轮补点/收敛与停止判据；候选点可由 `scripts/main.py doe` 复现
- [ ] 3 版候选方案已逐版可行性判定、成本算术与 Pareto 排序（若提供）
- [ ] 成本与时间预算有明细口径，多目标取舍说明"牺牲多少可接受"
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处置覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认标注 pending_human
- [ ] result.json：case_id 正确、status 如实、sources/uncertainty/change_log 齐全
- [ ] 阶段 0 骨架已落盘；validate_plan 阻断错误为 0；main.py check 退出码 0 或仅警告

## 7. 降级策略（禁止编造）

- 材料不足：无法确定的参数/响应/约束写入 uncertainty 与 limitations，status=insufficient_evidence。
- 越界/冲突：目标温度与材料耐温冲突等写入 risks/exception_handling；validate_plan 阻断错误必须解决或明确降级（降目标、换材料、转人工），不得悄悄改约束。
- 输入损坏：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读则 status=input_error。
- 缺软件：脚本只依赖标准库；PDF/PNG 预览缺工具时 artifacts 只保留真实生成的格式并在 limitations 说明。
- 时间/请求不足：阶段 0 骨架已落盘，按 0→1→2→3 增量改进，超时即停并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 8. 脚本与参考

- `scripts/main.py`：init（发现输入+立即落盘骨架）/ evaluate（候选方案可行性+成本算术）/ doe（确定性候选点）/ rank（Pareto 排序）/ finalize（合并 plan.json→result.json 并生成 plan.md）/ check（校验 result.json 契约）。
- `scripts/validate_plan.py`：schema + 可行域/硬阈值/成本算术校验，exit 0=通过、1=阻断、2=警告。
- `scripts/selftest.py`：脚本自检，合成最小输入验证 init/evaluate/doe/finalize/check/确定性/空输入/坏 JSON。
- `references/output_contract.md`：plan.json 与 result.json 全部字段定义。
- `references/rules.md`：参数识别/响应口径/搜索策略/成本权衡/风险处置规则与清单。
