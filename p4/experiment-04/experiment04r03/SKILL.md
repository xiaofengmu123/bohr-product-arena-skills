---
name: sci-plan-comparison
description: 在固定研究目标、资源/价格/排期硬约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择；统一七类字段与五维评分口径，执行真实算术、硬阈值淘汰、权重敏感性与证据溯源，输出可执行方案、比较矩阵与 result.json。本版本以“自检与确定性”为设计重心：scripts/selftest.py 保证同输入两次运行字节级一致。当任务要求“比较并选择/优化实验方案”时使用。
version: 2.0.0
---

# 实验方案比较与选择 Skill（自检与确定性版）

## 1. 角色与何时使用

把一个高频科研动作——“多个候选方案 → 可比矩阵 → 可辩护选择”沉淀为可复跑流程。适用：给定研究目标、硬约束、材料与通常 3 个候选方案，需要横向比较并给出选择、舍弃理由、风险与确认项。不适用：单方案从零设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

本版核心取舍：**不追求字段最多，而追求可复现**。所有输出排序确定、无墙钟时间戳、无随机；`run_id` 由输入内容哈希派生。`scripts/selftest.py` 逐项自检“同输入两次产物字节一致”。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、约束、通常 3 个版本化候选方案）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录的 PDF/文本/CSV（原论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造内容；PDF 用平台 `pdf-text-tool`/`pdftotext` 提取文本后再逐字引用。
- 脚本递归发现字段，不假设固定键名；`case.json` 中如有 `output_contract`，其全部键必须原样回填到 result.json（缺值填 `null`）。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

至少包含：`case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`；题面 `output_contract` 的**全部字段**（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）；`sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`change_log`；`research_question`、`comparison_frame`、`candidates[]`、`comparison_matrix`、`selection`、`confirmations`、`errors`、`degradations`、`pending_human`、`runtime`、`artifacts`。

每个候选方案统一填 **七类字段**（assumptions 假设、variables 变量、controls 对照、steps 步骤、parameters 参数、resources 资源、interpretation_criteria 判读标准）+ 成本/时间分解、可行性、风险、证据、五维评分（goal_fit/risk/cost/feasibility/evidence，0–5）。

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径同样含准备/排期/返工。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法 result.json 骨架，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | result.json 骨架 + case_pack.json | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选、约束、报价/排期 | case_pack.json、/input | 证据笔记（locator+quote） | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（七类字段、五维评分逐方案填写，附证据） | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble`，产出最终 result.json 与矩阵 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 5 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

第 1 步由脚本保证 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（坏输入 `input_error`，缺材料 `insufficient_evidence`）。第 3 步写 analysis.json 前先确认 result.json 已存在。

analysis.json 是 Agent 与脚本的接口：`candidates[].seven_fields`、`dimension_ratings`、`feasibility`、`risks`、`evidence`、`cost_override`、`time_override`；`selection`、`confirmations`、`sources`、`contract_fills`、`change_log`。`cost_override`/`time_override` 用于以真实报价/排期覆盖脚本通用算术并写 note+evidence，**不编造数字**。脚本自动完成：成本/时间算术、硬阈值淘汰（超预算、超时间窗、设备不可得）、加权评分、权重 ±0.05 敏感性、矩阵与 SVG/CSV/MD/PDF 产物。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果**
- [ ] 所有候选方案填了同一张七类字段矩阵，口径一致；给出了选择与舍弃理由
- [ ] 每个数字/参数范围/结论都能指回 locator+quote，无凭空数值
- [ ] 七类字段 + 目标/风险/成本/可行性/证据五维齐全

**使用增益**
- [ ] 机时/耗材/人力/准备/排期/返工六项成本与时间可核算，一次性与按次分开

**创新增益**
- [ ] 硬阈值淘汰 + 权重 ±0.05 敏感性已执行，选择在扰动下仍可辩护

**可靠体验**
- [ ] `inspect` 与 `assemble` 各跑通，`validate` 返回 `valid=true`
- [ ] `python scripts/selftest.py` 全部 PASS；同输入两次产物字节一致（无随机、确定性排序、无墙钟时间戳）

**异常处理**
- [ ] 设备不可得/超预算/参数冲突/全部不可行时 status 如实标 `partial` 且列出 pending_human

**生态贡献**
- [ ] result.json、comparison_matrix.svg/csv、plan_comparison.md、comparison_matrix.pdf 齐备可打开（PDF 以 `%PDF-` 开头）
- [ ] SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 6. 降级策略（诚实优先，禁止编造）

- 缺 case.json → `input_error`；坏 JSON → 记录 errors，status=`input_error`，退出码 0。
- 有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏编码 → 记录到 degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 脚本算术无法匹配报价表 → cost.method=`not_computable`、total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null，rationale 说明，status=`partial`。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 7. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo               # 接口示例（输出标注 is_example）
python scripts/selftest.py                # 自检：确定性 + 边界 + 产物合法性
```

默认目录 `/input`、`/app/output`、`/app/work`，可用 `--input/--output/--work` 或环境变量覆盖。`change_log.time` 与 `runtime.run_id` 取自输入自带日期或输入内容哈希，不使用墙钟时间，保证重复运行字节一致。
