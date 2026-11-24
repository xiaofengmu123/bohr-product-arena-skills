---
name: sci-plan-comparison
description: 在固定研究目标、资源/价格/排期约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择。统一七类字段与五维评分口径，执行真实算术、硬阈值淘汰、权重敏感性、证据逐字核验，并把不确定性、局限与降级状态显式写出，缺数据不编造。输出可执行方案、比较矩阵与 result.json。当任务要求“比较并选择/优化实验方案”时使用。
version: 2.0.0
---

# 实验方案比较与选择 Skill（v2：降级与不确定性显式化）

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”沉淀为可复跑的流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需要比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从头设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

本版相较通用模板的侧重点是**降级与不确定性**：每个数值区分 measured / contract / estimated / derived / not_reported；每个结论显式标注 confidence 与依赖假设；材料不足、报价缺失、设备未确认时如实降级并写 `limitations`/`pending_human`，绝不补造软件状态或科学数据。

核心失败模式要避免：各方案各写一套结构；不核对设备/预算/货期；时间只算执行不算准备与返工；成本漏机时与人力；风险无触发条件与后果量级；参数无来源；结论强度靠语气。本 Skill 强制统一口径、挂证据、做算术、写不确定性。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、约束与候选方案，可能含 `output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV（原论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造内容；PDF 先用 `pdftotext` 或平台 `pdf-text-tool` 提取为文本，再以提取后的文本文件作为证据 locator（不要指向原始 PDF）。
- 脚本可识别字段，不假设固定键名；`case.json` 中如有 `output_contract`，其全部键必须原样回填到结果。
- 工作目录 `/app/work`，产物目录 `/app/output`；脚本路径相对包根写为 `scripts/main.py`。目录可用 `--input/--output/--work` 或环境变量 `SKILL_INPUT/SKILL_OUTPUT/SKILL_WORK` 覆盖。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）
- `sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`limitations`、`change_log`（含时间戳的增量记录）
- `research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准/权重）
- `candidates[]`：每个方案统一填**七类字段**（assumptions 假设、variables 变量、controls 对照、steps 步骤、parameters 参数、resources 资源、interpretation_criteria 判读标准）+ 成本/时间分解、可行性、风险、证据、五维评分、不确定性
- `comparison_matrix`（字段×方案的归一化矩阵）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `confirmations`（安全/伦理/设备/专家确认项，未确认标 pending）、`errors`、`degradations`、`pending_human`、`runtime`、`artifacts`

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径同样包含准备/排期/执行/返工。数字须可核算并标来源与 `value_kind`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | 运行 `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料（PDF/合同），核对候选方案、约束、报价 | case_pack.json、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（见第 5 节），七类字段与五维评分逐方案填写，附 locator+quote | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | 运行 `python scripts/main.py assemble`，产出最终 result.json 与矩阵 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | 运行 `python scripts/main.py validate /app/output/result.json`，按第 6 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间做增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

> 合计 900s / 64 次。第 1 步由脚本保证在 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（坏输入标 input_error，无候选标 insufficient_evidence）。第 3 步写 analysis.json 前，先保证 result.json 已存在。

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
    "evidence":[{"claim":"…","locator":"materials/xxx.md#p3","quote":"逐字引用","kind":"literature|manual|quote|pilot|log","value_kind":"measured|contract|estimated|derived|not_reported"}],
    "uncertainty":[{"item":"…","basis":"…","impact":"…","confidence":"high|medium|low"}],
    "infeasible_reasons":[],
    "cost":{"currency":"CNY","lines":[{"part":"equipment_time","amount":0,"mode":"one_time|per_run","evidence":"…"}],"note":"…"},
    "cost_override":{"total":0,"currency":"CNY","lines":[],"note":"…","evidence":"…"},
    "time":{"unit":"h","parts":{"prep":0,"scheduling":0,"execution":0,"rework":0},"note":"…"},
    "time_override":{"total":0,"unit":"h","parts":{},"computed":true}
  }],
  "selection":{"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}],"sensitivity":{}},
  "confirmations":[{"item":"…","type":"safety|ethics|equipment|expert","status":"pending|confirmed","note":"…"}],
  "uncertainty":[],"limitations":[],"sources":[],"contract_fills":{},
  "change_log":[{"time":"…","step":"…","note":"…"}],
  "errors":[],"degradations":[],"pending_human":[],"model_requests_used":null
}
```

- 五维评分 0–5（5 最优）；维度缺失时脚本自动重新归一化权重并标 `uncertainty`，不崩。
- `cost_override`/`time_override` 用于纠正脚本的通用算术——Agent 读了真实报价/排期后以手工核对的数字覆盖，并写 note+evidence。**不编造数字**。
- 每个关键结论/数值/参数范围必须挂 `evidence`（locator + 逐字 quote）；脚本会**逐字核验 quote 是否出现在被定位文件中**，未命中标 `quote_verified=false`。材料没给就写 pending_human/not_reported。
- 脚本自动做：成本/时间算术、硬阈值淘汰（超预算、超时间窗、设备不可得）、加权评分、排名、敏感性（权重 ±0.05 扰动）、矩阵与 SVG/CSV/MD/PDF 产物。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选方案填了同一张七类字段矩阵，可比口径一致；选择了方案并给出舍弃理由
- [ ] 实际效果：比较中的每个数字/参数范围/结论都能指回 locator+quote，quote 逐字可核验，无凭空数值
- [ ] 实际效果：覆盖 假设/变量/对照/步骤/参数/资源/判读标准 七类字段 + 目标/风险/成本/可行性/证据 五维
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间都可核算、区分一次性与按次
- [ ] 创新增益：硬阈值淘汰规则 + 权重敏感性已执行，选择结论在扰动下仍可辩护
- [ ] 可靠体验：`python scripts/main.py inspect` 与 `assemble` 各跑通，`validate` 返回 valid=true
- [ ] 可靠体验：重复运行同输入结果一致（脚本无随机、确定性排序）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列出 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv、plan_comparison.md 齐备可打开；PDF 以 `%PDF-` 开头、`%%EOF` 结尾
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 7. 降级与不确定性策略（本版重点，诚实优先）

状态机：`ok`（证据完整且逐字核验通过）→ `partial`（有缺口但结论可用）→ `insufficient_evidence`（无候选/材料不足）→ `input_error`（缺 case.json/坏输入）。只允许向下如实降级，不因语气升级。

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏 JSON/坏编码 → 记录到 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 脚本算术无法匹配报价表时 → cost.method=`not_computable`，total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null，rationale 说明，status=partial。
- 所有限制写入 `limitations`（scope/cause/impact/mitigation），所有未决写入 `pending_human`，所有缺证据写入 `uncertainty`，禁止把缺失状态补造成事实。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo               # 接口自检，输出标注为“示例”
python scripts/selftest.py                # 运行 demo + 校验，退出码 0 为通过
```

## 9. 边界

- 不做“通用大而全”的 Skill：只解决“候选实验方案比较与选择”一类任务。
- 没有真实材料时输出“材料不足”，不用示例填充正式结果；`demo` 输出仅用于接口演示且显式标注“示例”。
- 交付前先在不含作者个人文件的环境跑 `selftest.py`；正式案例只以 `/input` 事实材料为准。
