---
name: plan-comparison-evidence-ledger
description: 在固定研究目标、资源/价格/排期硬约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择；以“证据台账 + 方案规格”双中间表示统一七类字段与五维评分，执行真实算术、硬阈值淘汰、权重敏感性与证据溯源，输出 result.json、比较矩阵与可编辑产物。当任务要求“比较/选择/优化实验方案”时使用。
version: 1.0.0
---

# 实验方案比较与选择 Skill（证据台账版）

## 1. 角色与何时使用

把“多候选方案 → 统一可比矩阵 → 可辩护选择”的科研流程沉淀为可复跑流程。适用：给定研究目标、硬约束、材料与 2–5 个候选方案，需要横向比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从零设计、文献综述/全文翻译、实验数据统计建模（仅作背景引用，不计为主要产出）。

核心失败模式要避免：各方案各写一套结构、不核对设备/预算/货期、时间只算执行、成本漏机时与人力、风险无触发条件与后果量级、参数无来源、结论强度靠语气。因此本 Skill 强制统一口径、挂证据、做算术。

**与常见做法不同**：本包把“证据”与“评价”拆成两份中间表示（见第 5 节），脚本先校验中间表示、再校验最终结果（两段式校验），保证每个数值都能指回材料中的逐字片段。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、可能含 `output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV/合同（原论文、报价、排期等）。**必须读实际文件**，不按文件名标签臆造内容；PDF 用平台 `pdf-text-tool`（或 `pdftotext`）提取后再读，无预提取答案层。
- 脚本不假设固定键名：`output_contract`、`candidates`、约束会在 JSON 里递归发现；找不到时按默认契约回填。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**原样回填（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）
- `sources`（每条 `{id, claim, locator, quote, kind, confidence}`）、`uncertainty`、`change_log`（含时间戳增量记录）
- `research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准）
- `candidates[]`：每个方案统一填 **七类字段**（assumptions 假设、variables 变量、controls 对照、steps 步骤、parameters 参数、resources 资源、interpretation_criteria 判读标准）+ 成本/时间分解、可行性、风险、证据引用、五维评分
- `comparison_matrix`（字段×方案矩阵与归一化分数）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `confirmations`（安全/伦理/设备/专家确认项，未确认标 pending）、`errors`、`degradations`、`pending_human`、`runtime`、`artifacts`、`limitations`

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径同样包含准备/排期/执行/表征/返工。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | 运行 `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料（PDF/合同），核对候选方案、约束、报价 | case_pack.json、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/evidence_ledger.json`（证据台账，见 5.1），每条结论挂 locator+逐字 quote | 材料 | evidence_ledger.json | ≤180s / ≤15 次 |
| 4 | 写 `/app/work/plan_spec.json`（方案规格，见 5.2），七类字段与五维评分逐方案填写，引用台账条目 | 台账 | plan_spec.json | ≤180s / ≤18 次 |
| 5 | 运行 `python scripts/main.py assemble`，产出最终 result.json 与矩阵产物 | case_pack + 台账 + 规格 | /app/output 全部产物 | ≤120s / ≤5 次 |
| 6 | 运行 `python scripts/main.py validate /app/output/result.json`，按第 6 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |

> 合计 ≤900s / ≤64 次。第 1 步由脚本保证 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（坏输入标 input_error，缺材料标 insufficient_evidence）。写台账/规格前先确认 result.json 已存在。

## 5. 中间表示（Agent 与脚本的接口）

### 5.1 evidence_ledger.json（证据台账，先写）

```json
{
  "case_id": "…",
  "entries": [
    {
      "id": "E1",
      "claim": "该证据支撑的结论/数值",
      "locator": "materials/quote.pdf#p2 或 materials/spec.csv#L12",
      "quote": "逐字引用",
      "kind": "literature|manual|quote|contract|pilot|log",
      "confidence": "high|medium|low",
      "conflicts_with": ["E2"]
    }
  ]
}
```

- 每个关键结论/数值/参数范围必须有台账条目；两个来源不一致时两个都记并标 `source_conflict`（在 conflicts_with 互指）。不编造；材料没给就写 pending_human/not_reported。

### 5.2 plan_spec.json（方案规格，后写）

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…","variables":{"independent":[],"dependent":[],"controlled":[]},"hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[{"name":"budget","op":"<=","value":0,"unit":"CNY"}],"criteria":["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit":0.25,"feasibility":0.25,"evidence":0.20,"cost":0.15,"risk":0.15},
  "candidates": [
    {
      "plan_id":"P1","name":"…","version":"v1",
      "seven_fields":{"assumptions":"…","variables":"…","controls":"…","steps":"…","parameters":"…","resources":"…","interpretation_criteria":"…"},
      "dimension_ratings":{"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
      "feasibility":{"equipment_available":"yes|no|partial","lead_time_ok":"yes|no|unknown","notes":"…"},
      "risks":[{"trigger":"…","consequence":"…","magnitude":"…","mitigation":"…","fallback":"…"}],
      "evidence_refs":["E1"],
      "cost":{"currency":"CNY","method":"computed|override|not_computable","total":null,"lines":[{"item":"…","amount":0,"unit":"CNY","per":"once|run","runs":null}],"note":"…"},
      "time":{"unit":"h","method":"computed|override|not_computable","total":null,"parts":{"prep":0,"scheduling":0,"execution":0,"characterization":0,"rework":0},"note":"…"}
    }
  ],
  "selection":{"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}],"sensitivity_notes":"…"},
  "confirmations":[{"item":"…","type":"safety|ethics|equipment|expert","status":"pending|confirmed","note":"…"}],
  "uncertainty":[{"item":"…","level":"low|medium|high","note":"…"}],
  "change_log":[{"time":"…","step":"…","note":"…"}]
}
```

- 五维评分 0–5（5 最优；cost/risk 也按“越高越好”记：5=低成本/低风险）；维度缺失时脚本重新归一化权重。
- `cost`/`time` 用 `method` 区分脚本算术与手工覆盖；Agent 读了真实报价/排期后以核对过的数字填 `total` 并写 `note`，**不编造数字**。
- 脚本自动做：成本/时间算术、硬阈值淘汰（超预算、超时间窗、设备不可得）、加权评分、排名、敏感性（权重 ±0.05 扰动）、矩阵与 SVG/CSV/MD/PDF 产物。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选方案填了同一张七类字段矩阵，可比口径一致；选择了方案并给出舍弃理由
- [ ] 实际效果：比较中的每个数字/参数范围/结论都能指回台账 locator+quote，无凭空数值
- [ ] 实际效果：覆盖 假设/变量/对照/步骤/参数/资源/判读标准 七类字段 + 目标/风险/成本/可行性/证据 五维
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间都可核算、区分一次性与按次
- [ ] 创新增益：硬阈值淘汰规则 + 权重敏感性已执行，选择结论在扰动下仍可辩护
- [ ] 可靠体验：`python scripts/main.py inspect` 与 `assemble` 各跑通，`validate` 返回 valid=true
- [ ] 可靠体验：`python scripts/main.py selftest` 全部 PASS；重复运行同输入结果一致（无随机、确定性排序）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列出 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv、plan_comparison.md 齐备可打开；PDF 以 `%PDF-` 开头
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 7. 降级策略（诚实优先）

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏 JSON/坏编码 → 记录 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 脚本算术无法匹配报价表 → cost/time.method=`not_computable`，total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null 或最高可行者，rationale 说明，status=partial。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py assemble           # 第 5 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py selftest           # 自检：空输入/坏 JSON/缺材料/确定性/产物格式
python scripts/main.py demo               # 最小调用示例（输出标为示例，不冒充正式案例）
```

- 目录缺省 `/input`、`/app/output`、`/app/work`，可用 `--input/--output/--work` 或环境变量 `SKILL_INPUT/SKILL_OUTPUT/SKILL_WORK` 覆盖。
- 台账与规格用任意文件写入工具落到 `/app/work/`；脚本读取时容错坏 JSON 并记录到 errors。
