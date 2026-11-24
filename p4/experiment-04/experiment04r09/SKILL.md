---
name: experiment-plan-matrix
description: 在固定研究目标、资源/价格/排期约束与多个候选实验方案之间做可复核的横向比较与选择；统一七类字段与五维评分口径，执行真实算术、硬阈值淘汰、敏感性分析与证据溯源，输出可执行方案、比较矩阵与 result.json。当任务要求“比较并选择/优化实验方案”时使用。
version: 1.0.0
---

# 实验方案比较与选择 Skill

## 1. 角色与何时使用

把「多个候选方案 → 同一张可比矩阵 → 可辩护选择」的科研流程沉淀为可复跑流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需要比较并给出选择、舍弃理由、风险与待确认项。不适用：单一方案从零设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计主要产出）。

必须避免的失败模式：各方案各写一套结构、不核对设备/预算/货期、时间只算执行不算准备与返工、成本漏机时与人力、风险无触发条件与后果量级、参数无来源、结论强度靠语气。本 Skill 强制统一口径、挂证据、做算术、诚实标注缺口。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束）、`/input/task.md`（题目说明）；材料在 `/input/` 及子目录。
- **必须读实际文件**，不按标签臆造；PDF 用平台 `pdf-text-tool`（或 `pdftotext`）提取后再读，不要相信无提取层的“答案”。
- `case.json` 的 `output_contract`（或 `outputContract`）全部键必须原样回填到 result.json，缺值给 `null`，不改名、不删未知键。
- 工作目录 `/app/work`，产物目录 `/app/output`；脚本相对路径 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

至少包含：`case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`；题面 `output_contract` 全部字段（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items`）；`sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`change_log`（含时间戳的增量记录）、`limitations`、`pending_human`。

另有：`research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准）、`candidates[]`（每方案统一 **七类字段**：assumptions/variables/controls/steps/parameters/resources/interpretation_criteria + 成本/时间分解、可行性、风险、证据、五维评分）、`comparison_matrix`、`selection`（chosen/ranking/rationale/rejected/sensitivity）、`confirmations`（未确认标 pending）、`errors`、`degradations`、`runtime`、`artifacts`。

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径含准备/排期/执行/返工。数字须可核算并标来源，无法核算时写 `not_computable`、`total=null` 并挂 `pending_human`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进，任何中途失败都不留空产物。**

| 步 | 动作 | 输入 | 输出 | 预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | result.json 骨架 + work/case_pack.json | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选、约束、报价/排期 | case_pack、/input | 证据笔记（locator+quote） | ≤240s / ≤15 次 |
| 3 | 写 `work/analysis.json`（见 references/schema.md），逐方案填七类字段与五维评分 | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble`，产出最终 result.json 与矩阵 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 5 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

> 第 1 步由脚本保证：inspect 无论输入好坏都写合法 result.json（坏输入标 `input_error`，无候选标 `insufficient_evidence`）。写 analysis.json 前先确保 result.json 已存在。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] 实际效果：所有候选填同一张七类字段矩阵，可比口径一致；有选择与舍弃理由
- [ ] 实际效果：每个数字/参数范围/结论能指回 locator+quote，无凭空数值
- [ ] 实际效果：覆盖七类字段 + 目标/风险/成本/可行性/证据五维评分
- [ ] 使用增益：六项成本与五项时间可核算、区分一次性与按次、可复核
- [ ] 创新增益：硬阈值淘汰 + 权重敏感性已执行，选择在扰动下仍可辩护
- [ ] 可靠体验：inspect/assemble 各跑通，validate 返回 valid=true
- [ ] 可靠体验：重复运行同输入结果一致（脚本无随机、排序确定性）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行 → status 如实标 partial 并列 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.{svg,csv}、plan_comparison.md 齐备；PDF 以 `%PDF-` 开头
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 6. 降级策略（诚实优先）

- 缺 case.json / 坏 JSON / 坏编码 → 记录到 errors/degradations，继续处理其余材料，不崩溃；无输入标 `input_error`。
- 有输入但无候选方案或材料不足 → `insufficient_evidence`，列出缺口，不编造方案或章节。
- 脚本算术无法匹配报价 → `cost.method=not_computable`、`total=null`，写 `pending_human`，不强行给数。
- 设备档期/安全/伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；`chosen=null` 或最高可行者，rationale 说明，`status=partial`。
- 缺软件状态或科学数据一律写入 `limitations`/`pending_human`，禁止补造成事实；不编造 DOI/数值/分数。

## 7. 脚本调用（相对路径，仅标准库）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py selftest           # 交付前自检
python scripts/main.py demo               # 最小调用示例（输出标注为示例）
```

字段定义与 analysis.json 契约见 `references/schema.md`；运行自检与失败处置清单见 `references/checklist.md`。
