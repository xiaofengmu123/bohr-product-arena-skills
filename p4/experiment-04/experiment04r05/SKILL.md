---
name: sci-plan-comparison-v2
description: 在固定研究目标、资源/价格/排期硬约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择；以题目 output_contract 为单一事实源逐字段回填并生成契约对齐审计，统一七类字段与五维评分，执行真实算术、硬阈值淘汰、权重敏感性与证据溯源，输出 result.json、比较矩阵与可编辑/可渲染工件。当任务要求“比较并选择/优化实验方案”时使用。
version: 1.0.0
---

# 实验方案比较与选择 Skill（契约优先版）

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”沉淀为可复跑的流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从头设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

本版的设计取舍：**契约优先**。不以固定键名假设结果结构，而是从 `/input/case.json` 的 `output_contract` 递归展开全部字段，逐项回填到 `result.json`，并输出 `contract_alignment` 审计块与带序号、只追加的 `change_log`，使“输出契约完整”可被机器核验而非依赖 Agent 自觉。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、`output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV/报价/排期文件。**必须读实际文件**，不按标签臆造内容；PDF 用平台 `pdf-text-tool`/`pdftotext` 提取后再读。
- 脚本递归发现字段，不假设固定键名；`case.json` 中如有 `output_contract`，其全部键必须原样回填；无显式契约时默认回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**（逐一对齐；缺值给 `null`/`[]`，不改名、不删未知键）
- `output_contract`（原样保存）+ `contract_alignment`（每个契约字段的路径/是否回填/来源/类型）
- `research_question`、`comparison_frame`、`candidates[]`、`comparison_matrix`、`selection`
- `experimental_plan`、`evidence_and_risks`、`confirmation_items`（默认三组的落点）
- `sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`confirmations`、`pending_human`
- `errors`、`degradations`、`limitations`、`runtime`、`artifacts`
- `change_log`：只追加、带 `seq` 与时间戳的增量记录（每次运行至少加一条，历史不重写）

成本口径：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径含准备/排期/执行/表征排队/返工。数字须可核算并标来源；无法核算时 `method=not_computable`、`total=null`，写入 `pending_human`，**不编造数字**。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法 partial 结果，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选方案、约束、报价 | case_pack、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（见第 5 节），七类字段与五维评分逐方案填写，附 locator+quote；其余契约字段填 `contract_fills` | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble`，产出最终 result.json、矩阵与工件 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 6 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

第 1 步由脚本保证在 3 分钟内落盘：`inspect` 无论输入好坏都写合法 result.json（坏输入标 `input_error`，缺材料标 `insufficient_evidence`）。第 3 步写 analysis.json 前先确认 result.json 已存在。

## 5. analysis.json 填写规范（Agent 与脚本的接口）

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…","variables":{"independent":[],"dependent":[],"controlled":[]},"hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[],"criteria":["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit":0.25,"feasibility":0.25,"evidence":0.20,"cost":0.15,"risk":0.15},
  "candidates": [{
    "plan_id":"P1","name":"…","version":"v1",
    "seven_fields":{"assumptions":"…","variables":"…","controls":"…","steps":"…","parameters":"…","resources":"…","interpretation_criteria":"…"},
    "dimension_ratings":{"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
    "feasibility":{"equipment_available":"yes|no|partial","lead_time_ok":"…","notes":"…"},
    "risks":[{"trigger":"…","consequence":"…","magnitude":"…","mitigation":"…","fallback":"…"}],
    "evidence":[{"claim":"…","locator":"materials/xxx.md#p3","quote":"逐字引用","kind":"literature|manual|quote|pilot|log"}],
    "infeasible_reasons":[],
    "cost_override":{"total":0,"currency":"CNY","lines":[],"note":"…","evidence":"…"},
    "time_override":{"total":0,"unit":"h","parts":{},"computed":true}
  }],
  "selection":{"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}],"sensitivity":{}},
  "confirmations":[{"item":"…","type":"safety|ethics|equipment|expert","status":"pending|confirmed","note":"…"}],
  "uncertainty":[],"sources":[],"contract_fills":{}, "change_log":[],
  "errors":[],"degradations":[],"pending_human":[]
}
```

- 五维评分 0–5（5 最优；`risk`/`cost` 按“越有利分越高”计，5=低风险/低成本）。维度缺失时脚本对该方案重新归一化权重。
- `cost_override`/`time_override` 用于纠正脚本通用算术——Agent 读真实报价/排期后以手工核对数字覆盖，写 note+evidence。
- 每个关键结论/数值/参数范围必须挂 `evidence`（locator + 逐字 quote）；材料没给就写 `pending_human`/`not_reported`。
- `contract_fills` 用于回填 output_contract 中除标准字段外的任意字段（键为契约字段名，值为其内容）。
- 脚本自动做：成本/时间算术、硬阈值淘汰（超预算/超时间窗/设备不可得）、加权评分、排名、敏感性（权重 ±0.05 扰动）、契约对齐、矩阵与 SVG/CSV/MD/PDF 工件。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选方案填同一张七类字段矩阵，口径一致；给出选择与舍弃理由
- [ ] 实际效果：每个数字/参数范围/结论可指回 locator+quote，无凭空数值
- [ ] 实际效果：覆盖 假设/变量/对照/步骤/参数/资源/判读标准 七类字段 + 目标/风险/成本/可行性/证据 五维
- [ ] 输出契约：result.json 含 `case_id` 与 output_contract 全部字段；`contract_alignment` 无 missing（或 missing 已入 pending_human）
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间可核算，区分一次性与按次
- [ ] 创新增益：硬阈值淘汰 + 权重敏感性已执行，选择结论在扰动下仍可辩护
- [ ] 可靠体验：`inspect` 与 `assemble` 各跑通，`validate` 返回 valid=true，`python scripts/selftest.py` 全绿
- [ ] 可靠体验：重复运行同输入结果一致（无随机、确定性排序）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv、plan_comparison.md、comparison_matrix.pdf 齐备可打开；PDF 以 `%PDF-` 开头、`%%EOF` 结尾
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 7. 降级策略（诚实优先）

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列缺口，不编造章节或方案。
- 坏 JSON/坏编码 → 记入 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 算术无法匹配报价表 → `cost.method=not_computable`、`total=null`，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；`chosen=null` 或最高可行者，rationale 说明，`status=partial`。
- 契约字段无来源可填 → 填 `null`/`[]` 并在 `contract_alignment` 标 missing、写入 limitations，不补造。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect             # 第 1 步：骨架 + case_pack（前 3 分钟内落盘）
python scripts/main.py assemble            # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo                # 示例自检（输出标注为“示例”）
python scripts/selftest.py                 # 空目录/坏 JSON/缺输入/工件合法/确定性 全量自检
```
