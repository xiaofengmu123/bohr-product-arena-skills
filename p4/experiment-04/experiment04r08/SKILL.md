---
name: experiment-plan-comparison
description: 在固定研究目标、硬约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择。统一七类可比字段（假设/变量/对照/步骤/参数/资源/判读标准）与五维评分（目标契合/风险/成本/可行性/证据），执行真实算术、硬阈值淘汰、权重敏感性、证据溯源（locator+逐字引用），并把题目的五个评分维度逐条映射到流程与自检项。当任务要求"比较并选择/优化实验方案"时使用。
version: 1.0.0
---

# 实验方案比较与选择（评分维度对齐版）

## 1. 角色与何时使用

把"多个候选方案 → 同一张可比矩阵 → 可辩护选择"的科研流程沉淀为可复跑 Skill。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需要比较并给出选择、舍弃理由、风险与确认项。不适用：单方案从头设计、文献综述/全文翻译、统计建模（只作背景引用）。

必须避免的失败模式：各方案各写一套结构；不核对设备/预算/货期；时间只算执行不算准备与返工；成本漏机时与人力；风险无触发条件与后果量级；参数无来源；结论强度靠语气。本 Skill 强制统一口径、挂证据、做算术。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、可选 `output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV（论文、报价、排期合同）。**必须读实际文件**，不按标签臆造内容；PDF 用平台 `pdf-text-tool`/`pdftotext` 提取后再读。
- `case.json` 如有 `output_contract`，其全部键必须原样回填到 result.json（缺值给 null/[]，不改名、不删键）。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

result.json 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的全部字段（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）
- `sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`change_log`（含时间戳增量记录）
- `research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准/权重）
- `candidates[]`：每方案统一填七类字段 + 成本/时间分解 + 可行性 + 风险 + 证据 + 五维评分
- `comparison_matrix`（同一组行 × 方案的矩阵与评分）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `confirmations`（安全/伦理/设备/专家确认项，未确认标 pending）
- `errors`、`degradations`、`pending_human`、`limitations`、`runtime`、`artifacts`
- `rubric_map`：五个评分维度 → 分值/评分点 → 产出字段 → 自检项（本版重点，见 §7）

成本口径统一：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径含准备/排期/执行/返工。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘合法结果骨架，之后只做增量改进。** 中途失败不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` + `/app/work/analysis.json`（模板） | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选方案/约束/报价 | case_pack、/input | 证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（见 §5），七类字段与五维评分逐方案填，附 locator+quote | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble`，产出最终 result.json 与矩阵 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按 §6/§8 自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

> 合计 900s / 64 次。第 1 步 inspect 无论输入好坏都在 3 分钟内写合法 result.json（坏输入标 input_error，缺材料标 insufficient_evidence）。第 3 步写 analysis.json 前，先保证 result.json 已存在。

## 5. analysis.json 填写规范（Agent 与脚本的接口）

完整 schema 见 `references/rubric-mapping.md`。要点：

- 每个候选方案填 `seven_fields` 七项、`dimension_ratings` 五项（0–5，5 最优）、`feasibility`、`risks`、`evidence`、`cost`、`time`。
- 成本六项/时间四项写成 `components` 数字；Agent 读真实报价后可用 `cost_override`/`time_override` 覆盖，写 note+evidence。**不编造数字**。
- 每个关键结论/数值/参数范围挂 `evidence`（locator + 逐字 quote）；材料没给写 pending_human/not_reported。
- 脚本自动做：成本/时间求和、硬阈值淘汰、加权评分、排名、敏感性（权重 ±0.05 扰动）、矩阵与 CSV/SVG/MD/PDF 产物、rubric_map 生成。

## 6. 硬阈值与选择规则

脚本按以下规则淘汰（规则与结果写入 `selection.decision_rules`）：

1. 设备不可得（`feasibility.equipment_available == "no"`）→ 不可行；
2. 货期不满足（`feasibility.lead_time_ok == "no"`）→ 不可行；
3. 成本超预算（`cost.total > budget`）→ 不可行；
4. 时间超窗口（`time.total > time_window`）→ 不可行；
5. 成本/时间 `method == "not_computable"` → 不自动淘汰，标 unresolved 并挂 pending_human。

可行方案按加权分排序；`analysis.selection.chosen` 若在可行集内则采用，否则取最高分者。候选全部不可行时 chosen=null、status=partial。

## 7. 评分维度对齐（本版设计重心）

`rubric_map` 把题目五个评分维度逐条映射到"流程步骤 → 产出字段 → 自检项"，使任何一条分数都有可追溯落点：

| 评分维度 | 分值 | 本 Skill 对应产出 | 主要自检项 |
|---|---|---|---|
| 实际效果 | 40 | comparison_matrix、selection、sources、evidence_ledger | 七类字段同表可比；每数挂 locator+quote；选择可辩护 |
| 使用增益 | 25 | 统一模板、cost/time 自动核算、evidence 模板 | 减少对齐字段/查单价/核机时/返工；对照可验证 |
| 创新增益 | 15 | 硬阈值规则、评分矩阵、敏感性、evidence 挂载 | 淘汰规则与敏感性确实执行且改变结论边界 |
| 可靠体验 | 10 | inspect/assemble/validate/selftest、降级策略 | 空输入/坏 JSON/缺参不崩；重复运行一致 |
| 生态贡献 | 10 | SKILL.md、rubric-mapping.md、checklist.md、可复用模板 | 他人可独立复跑；产物齐备可打开 |

最终 result.json 的 `rubric_map` 由脚本生成并随结果落盘，便于复核者逐条核对。

## 8. 自检清单（逐条可勾选，与 rubric_map 对应）

- [ ] 所有候选方案填同一张七类字段矩阵，口径一致；选择了方案并给出舍弃理由
- [ ] 每个数字/参数范围/结论都能指回 locator+quote，无凭空数值
- [ ] 覆盖七类字段 + 五维评分；成本六项/时间四项可核算、区分一次性与按次
- [ ] 硬阈值淘汰与权重敏感性已执行，选择在扰动下仍可辩护
- [ ] `inspect`、`assemble`、`validate` 各跑通，validate 返回 valid=true
- [ ] 重复运行同输入结果一致（脚本无随机、确定性排序）
- [ ] 设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列 pending_human
- [ ] result.json、comparison_matrix.csv/svg、plan_comparison.md、comparison_matrix.pdf 齐备可打开；PDF 以 `%PDF-` 开头、`%%EOF` 结尾
- [ ] SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 9. 降级策略（诚实优先）

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏 JSON/坏编码 → 记录到 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 脚本算术无法匹配报价表 → cost/time.method=`not_computable`，total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null 或最高可行者，rationale 说明，status=partial。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 10. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack + analysis 模板
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo               # 自检示例，标注"示例"
python scripts/selftest.py                # 交付前自检
```

可用 `--input/--output/--work` 覆盖默认路径（`/input`、`/app/output`、`/app/work`）。
