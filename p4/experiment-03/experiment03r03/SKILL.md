---
name: weekly-skill
description: >-
  实验参数优化建议 Skill：把固定研究目标与设备/材料/安全/成本约束转成可执行、
  可复核、可复现的参数方案。产出关键参数与可行区间、响应指标口径、搜索策略、
  成本时间预算、风险与异常处置，并输出带逐字溯源与不确定性的 result.json
  （含 case_id、status、sources、uncertainty、change_log、input_fingerprint）。
  当任务是把科研材料转成"可开机的实验参数方案"时使用。本版强化自检与确定性：
  scripts/selftest.py 验证同输入两次产物逐字节一致。
version: 2.0.0
---

# 实验参数优化建议 Skill（自检与确定性版）

## 1. 角色与何时使用

面向**有界参数优化**：参数有有限定义域、有明确设备量程/材料耐温/安全阈值、
有可测量响应指标。交付物不是一段解释，而是一份可开机方案。

适用：给定研究目标 + 设备材料约束 + 预算，产出参数方案并独立复核。
不适用：无材料无约束的头脑风暴、纯文献综述、只需照抄既有 SOP 的情形。

核心目标（对应评分维度）：①建议参数落在真实可行域内；②每条权衡有响应指标与
成本口径支撑；③候选点产生/每轮评估/取舍/停止判据可复核；④四项全覆盖
（关键参数、响应指标、搜索策略、实验成本）；⑤诚实标注依据、假设与确认项，
不编造数据；⑥同输入两次产物一致。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例标识 + 约束元数据）、`/input/task.md`、
  按 topic 提供的科研材料/PDF。
- **必须实际读取材料内容**，不得依据文件名/领域标签臆测；PDF 用平台
  `pdf-text-tool` 读原文，不假设存在预提取答案层。
- 工作目录 `/app/work`，最终产物写入 `/app/output/`（含 result.json）。
- 预算：900 秒、64 次模型请求、单次响应含推理 ≤16384 token；3 个案例独立运行。

## 3. 输出契约

必须产出 `/app/output/result.json`（字段定义见 `references/output_contract.md`），至少含：

- `case_id`（取自 `/input/case.json`）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `plan`：主结果，覆盖假设、变量、对照、步骤、参数、资源、判读标准、响应指标、
  搜索策略、成本预算、依据、风险、异常处置、确认项
- `sources`：证据映射，每条含 `locator` + 逐字 `quote`
- `uncertainty` / `limitations` / `pending_human` / `change_log` / `errors` /
  `degradations` / `artifacts` / `input_fingerprint` / `revision`

主产物 `plan.md` 可打开可编辑，`plan.json` 为结构化规格，二者与 result.json 路径
互相对应且实际存在；`candidates.json` 由 `doe` 生成后一并交付。

## 4. 执行流程（总 900 秒 / 64 请求）

### 阶段 A：0–3 分钟（≤180 秒，≤8 请求）——必须先落盘骨架

1. `python scripts/main.py init --input /input --output /app/output --work /app/work`
2. 读 `/app/work/input_digest.json`，并实际读取 `/input/case.json` + `task.md` + 材料。
3. 提取研究目标/变量/约束/响应指标第一版，写入 `/app/work/plan.json`。
4. `python scripts/check.py validate-plan /app/work/plan.json --report /app/work/plan.validation_report.json`
5. `python scripts/main.py finalize --input /input --output /app/output --work /app/work`
   写骨架：status=partial、case_id 已填、字段齐全、change_log 首条"骨架落盘"。
   **此步不得跳过**：超时/中断仍可交付部分结果。

### 阶段 B：3–10 分钟（≤30 请求）——方案建模与约束校验

6. 逐条核定关键参数：name/unit/suggested/min/max/step，每条附 `constraint_source`
   + `basis_locator` + 逐字 `basis_quote`。
7. 定义响应指标口径（definition/unit/method/direction/target/accept_lower/
   prediction_method/uncertainty），披露预测方式与不确定度。
8. 选定搜索策略（网格/序贯/贝叶斯/分阶段筛选），写明 initial_points、per_round、
   stop_criteria、candidate_count、seed。
9. 每次修改后重跑 `validate-plan`，阻断错误清零；再跑
   `python scripts/main.py doe --work /app/work --seed 0` 生成确定性候选点。

### 阶段 C：10–14 分钟（≤20 请求）——成本、风险与自检

10. 补全 cost_budget（items/total/time_hours/tradeoff_note）与 risks /
    exception_handling / confirmations。
11. 补全 sources 与 uncertainty / limitations / pending_human。
12. `python scripts/check.py result /app/output/result.json --output-dir /app/output`，逐条修复。

### 阶段 D：14–15 分钟（≤6 请求）——收尾

13. 按实际内容设置 status（或写入 `status_hint`）；重跑 finalize + check；更新 change_log。

脚本调用一律用相对 Skill 根 `/skills/weekly-skill` 的相对路径；预算只作上限，不必用完。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 已实际读取 /input/case.json + task.md + 材料内容（非仅文件名/标签）
- [ ] 关键参数含 name/unit/suggested/min/max/step，且全部落在量程/耐温/安全阈值内
- [ ] 每条参数有 `constraint_source` + `basis_locator` + 逐字 `basis_quote`
- [ ] 响应指标含口径/单位/方法/方向/目标/可接受下限，prediction_method 与 uncertainty 已披露
- [ ] 搜索策略写明 initial_points/per_round/stop_criteria；候选点可由 doe 复现
- [ ] 成本与时间预算有明细口径，tradeoff_note 写明"牺牲多少可接受"
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处置覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备/专家）已列，未确认内容进 pending_human
- [ ] result.json：case_id 正确、status 如实、sources/uncertainty/change_log 齐全、
      limitations 如实、input_fingerprint 已填
- [ ] check.py 退出码 0 或仅警告（警告已说明）
- [ ] 确定性：`python scripts/selftest.py` 退出码 0（同输入两次产物逐字节一致）

## 6. 确定性约定（本版重点）

- 产物**禁止写入墙钟时间**；change_log 用固定 step 标识，revision=条目数。
- 所有 JSON 用 `write_json`（sort_keys + LF 换行）写出；plan.md 按语义字段顺序渲染。
- `doe` 固定 seed、参数按名称排序、子采样用 `random.Random(seed).sample`。
- `finalize` 是纯函数：同一 plan.json + 同一输入，两次运行产物逐字节一致。
- 由 `scripts/selftest.py` 自动验证，不允许"差不多一致"。

## 7. 降级策略（禁止编造）

- 材料不足：无法确定的参数/响应/约束写入 uncertainty 与 limitations，
  status=insufficient_evidence，不编造数值与来源。
- 越界冲突：写入 risks/exception_handling；validate-plan 阻断错误必须解决或明确降级
  （降目标、换材料、转人工），不得悄悄改约束。
- 输入损坏：坏 JSON/坏编码捕获到 errors/degradations，继续处理其余材料；全部不可读
  则写 `status_hint=input_error`。
- 缺软件：脚本只依赖标准库；plan.md 无需外部依赖。无法生成的格式不伪造导出记录，
  在 limitations 说明。
- 时间/请求不足：先保证骨架已落盘，按 A→B→C→D 增量改进；超时即停止并保留当前 result.json。
- 任何情况下不得编造评测分数、伪造运行结果、补造科学数据或假文件路径。

## 8. 脚本与参考

- `scripts/main.py`：init / finalize / doe / fingerprint，确定性编排器（纯标准库）。
- `scripts/check.py`：validate-plan（schema + 可行域）+ result（契约 + 产物存在性），
  退出码 0=通过、1=阻断、2=仅警告。
- `scripts/selftest.py`：空输入/坏 JSON/缺输入/demo 四场景 + 确定性逐字节对比。
- `references/output_contract.md`：plan.json 与 result.json 全部字段定义。
- `references/rules.md`：参数识别/响应口径/搜索策略/成本权衡/风险处置/确定性规则与清单。
