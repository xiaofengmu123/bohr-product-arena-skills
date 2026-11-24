---
name: experiment-plan-comparison
description: 在固定研究目标、硬约束与多个候选实验方案之间做可复核的横向比较与选择；统一七类字段与五维评分口径，执行真实算术、硬阈值淘汰与权重敏感性分析，并把每条关键结论挂到 locator + 逐字 quote 的 evidence_map 上；输出 result.json、可比矩阵与可编辑产物。当任务要求“比较并选择/优化实验方案”时使用。
version: 1.0.0
---

# 实验方案比较与选择 Skill

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”的科研流程沉淀为可复跑、可复核的流程。适用：给定研究目标、硬约束、材料与通常 3 个候选方案，需要比较并给出选择、舍弃理由、风险与确认项。不适用：单方案从零设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

必须避免的失败模式：各方案各写一套结构；不核对设备/预算/货期；时间只算执行不算准备与返工；成本漏机时与人力；风险无触发条件与后果量级；参数无来源；结论强度靠语气。因此本 Skill 强制统一口径、挂证据、做算术。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束）与 `/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV（论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造内容；PDF 用平台 `pdf-text-tool` 或 `pdftotext` 提取后再读。
- 脚本递归发现字段，不假设固定键名；`case.json` 中如有 `output_contract`，其全部键必须原样回填到结果。
- 工作目录 `/app/work`，产物目录 `/app/output`；脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items`）
- `sources`、`uncertainty`、`change_log`（含步骤序号的增量记录）
- `research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准）
- `candidates[]`：每个方案统一填七类字段（assumptions/variables/controls/steps/parameters/resources/interpretation_criteria）+ 成本/时间分解、可行性、风险、证据、五维评分
- `comparison_matrix`（字段×方案矩阵）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `evidence_map`（逐条 claim：claim_id/subject/field/assertion_type/strength/locator/quote/source_ref/conflict，见 `references/evidence_map_spec.md`）
- `confirmations`（安全/伦理/设备/专家确认项，未确认标 pending）、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`、`artifacts`

成本口径统一为机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工六项，区分一次性投入与按次消耗；时间口径含准备/排期/执行/表征排队/返工五项。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选方案、约束、报价/排期 | case_pack、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（七类字段、五维评分、evidence_map、成本/时间） | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble` | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 6 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

合计 900s / 64 次。inspect 无论输入好坏都在前 3 分钟落盘：坏输入标 `input_error`，缺材料标 `insufficient_evidence`，有候选标 `partial`。第 3 步写 analysis.json 前，先保证 result.json 已存在。

## 5. analysis.json 接口（Agent 与脚本的接口）

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…","variables":{"independent":[],"dependent":[],"controlled":[]},"hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[{"name":"…","field":"cost_total|time_total|…","op":"<=","value":0}],"criteria":["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit":0.25,"feasibility":0.25,"evidence":0.20,"cost":0.15,"risk":0.15},
  "candidates": [{
    "plan_id":"P1","name":"…","version":"v1",
    "seven_fields":{"assumptions":"…","variables":"…","controls":"…","steps":"…","parameters":"…","resources":"…","interpretation_criteria":"…"},
    "dimension_ratings":{"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
    "feasibility":{"equipment_available":"yes|no|partial","lead_time_ok":"…","notes":"…"},
    "risks":[{"trigger":"…","consequence":"…","magnitude":"…","mitigation":"…","fallback":"…"}],
    "cost_lines":[{"category":"equipment_time|consumables|labor|sample_prep|scheduling|rework","item":"…","unit":"…","unit_price":0,"quantity":0,"one_off":false,"evidence":{"claim_id":"…","claim":"…","locator":"…","quote":"…","kind":"…","assertion_type":"…","strength":"…"}}],
    "time_parts":{"sample_prep":0,"scheduling":0,"execution":0,"queue":0,"rework":0,"unit":"h"},
    "cost_override":{"total":0,"currency":"CNY","lines":[],"note":"…","evidence":"…"},
    "time_override":{"total":0,"unit":"h","parts":{},"computed":true,"note":"…"},
    "evidence":[{"claim_id":"…","claim":"…","subject":"…","field":"…","locator":"…","quote":"…","kind":"literature|manual|quote|pilot|log","assertion_type":"fact|inference|estimate|assumption","strength":"established|preliminary|prospect|reported|not_reported","source_ref":"S1"}],
    "infeasible_reasons":[]
  }],
  "selection":{"chosen":"P2","rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}]},
  "confirmations":[{"item":"…","type":"safety|ethics|equipment|expert","status":"pending|confirmed","note":"…"}],
  "sources":[{"ref_id":"S1","title":"…","locator":"…","quote":"…","kind":"…"}],
  "frame_evidence":[], "selection_evidence":[], "conclusion_links":{},
  "contract_fills":{}, "uncertainty":[], "limitations":[], "pending_human":[],
  "change_log":[{"ts":"T+00s","seq":1,"step":"…","note":"…"}]
}
```

- 五维评分 0–5（5 最优）；维度缺失时脚本自动重新归一化权重。
- `cost_override`/`time_override` 用于纠正脚本通用算术——Agent 读真实报价/排期后以手工核对数字覆盖，并写 note+evidence；**不编造数字**。
- 每条关键结论/数值/参数范围挂 `evidence`（locator + 逐字 quote）；材料没给就写 pending_human/not_reported。
- 脚本自动做：成本/时间算术、硬阈值淘汰（超预算/超时间窗/设备不可得/显式硬约束）、加权评分、排名、敏感性（每维权重 ±0.05 扰动后重排名）、evidence_map 汇总与 SVG/CSV/MD/PDF 产物。

## 6. 自检清单（逐条勾选）

- [ ] 所有候选方案填同一张七类字段矩阵，可比口径一致；选择了方案并给出舍弃理由
- [ ] 比较中的每个数字/参数范围/结论都能指回 locator+quote，无凭空数值；evidence_map 每条 claim 有 claim_id
- [ ] 覆盖假设/变量/对照/步骤/参数/资源/判读标准七类字段 + 目标/风险/成本/可行性/证据五维
- [ ] 机时/耗材/人力/样品准备/排期/返工六项成本与时间都可核算、区分一次性与按次
- [ ] 硬阈值淘汰规则 + 权重敏感性已执行，选择结论在扰动下仍可辩护（sensitivity.robust）
- [ ] `inspect` 与 `assemble` 各跑通，`validate` 返回 `{"valid": true, ...}`
- [ ] 重复运行同输入结果一致（无随机、排序确定性）；`python scripts/selftest.py` 全 PASS
- [ ] 设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列出 pending_human
- [ ] result.json、comparison_matrix.svg/csv、plan_comparison.md 齐备可打开；PDF 以 `%PDF-` 开头
- [ ] SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 7. 降级策略（诚实优先）

- 缺 case.json / 坏 JSON → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏编码 / 超 2MB 单文件 → 记录到 errors/degradations，继续处理其余材料，退出码 0。
- 脚本算术无法匹配报价表 → `cost.method="not_computable"`、`total=null`，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；`chosen=null`，rationale 说明，status=partial。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo               # 示例（标注 EXAMPLE，非正式案例）
python scripts/selftest.py                # 健壮性自检
```

## 9. 边界

一个 Skill 只解决“方案比较与选择”这一类任务，不做通用大而全；没有真实材料时输出“材料不足”，不用示例填充正式案例；交付前先在不属于作者的环境里跑一遍 `selftest.py`。
