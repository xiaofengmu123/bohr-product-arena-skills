---
name: sci-plan-comparison
description: 在固定研究目标、资源/价格/排期约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择；统一七类字段（假设/变量/对照/步骤/参数/资源/判读标准）与五维评分（目标契合/风险/成本/可行性/证据），执行真实算术、硬阈值淘汰、权重敏感性与证据溯源（locator+逐字 quote），输出可执行方案、比较矩阵与 result.json。当任务要求“比较并选择/优化实验方案”时使用。本版为鲁棒性变体：空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级不崩溃，且前 3 分钟先落盘合法结果骨架。
version: 1.1.0
---

# 实验方案比较与选择 Skill

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”沉淀为可复跑流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需要横向比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从头设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

要避免的失败模式：各方案各写一套结构、不核对设备/预算/货期、时间只算执行不算准备与返工、成本漏机时与人力、风险无触发条件与后果量级、参数无来源、结论强度靠语气。因此本 Skill 强制统一口径、挂证据、做算术，并强制在失败前留下可用骨架。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、可能含 `output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV（原论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造内容；PDF 用 `pdf-text-tool` 或 `python scripts/main.py extract`（内部调 `pdftotext`）提取后再读。
- 脚本不假设固定键名：`candidates/plans/options/候选方案` 等均被递归发现；`case.json` 中如有 `output_contract`，其全部键必须原样回填。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少包含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）
- `sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`change_log`、`pending_human`、`limitations`、`errors`、`degradations`、`runtime`、`artifacts`
- `research_question`（可检验问题、自变量/因变量/控制变量、假设）、`comparison_frame`（目标/硬约束/评价标准/权重）
- `candidates[]`：每个方案统一填七类字段 + 成本/时间分解、可行性、风险、证据、五维评分
- `comparison_matrix`（字段×方案的归一化矩阵）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `confirmations`（安全/伦理/设备/专家确认项，未确认标 pending）

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径含准备/排期/返工。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进。任何中途失败都不留空产物。**

| 步 | 动作 | 输入 | 输出 | 时间/请求预算 |
|---|---|---|---|---|
| 1 | 运行 `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | 读 case_pack + 实际材料（PDF/合同），核对候选方案、约束、报价 | case_pack.json、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 `/app/work/analysis.json`（见 references/analysis-schema.md），七类字段与五维评分逐方案填写，附 locator+quote | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | 运行 `python scripts/main.py assemble`，产出最终 result.json 与矩阵 | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | 运行 `python scripts/main.py validate /app/output/result.json`，按第 6 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间做增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

第 1 步由脚本保证在 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（坏输入标 input_error，缺材料标 insufficient_evidence）。第 3 步写 analysis.json 前，先保证 result.json 已存在。

## 5. analysis.json 接口（详见 references/analysis-schema.md）

脚本负责确定性算术与阈值淘汰；Agent 负责从材料中提取七类字段、五维评分与证据。关键约定：五维评分 0–5；`cost_override`/`time_override` 用于以真实报价覆盖脚本算术（不编造数字）；每个数值/参数范围挂 `evidence`（locator + 逐字 quote）；材料没给就写 `pending_human`/`not_reported`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选方案填同一张七类字段矩阵，可比口径一致；选择了方案并给出舍弃理由
- [ ] 实际效果：比较中的每个数字/参数范围/结论都能指回 locator+quote，无凭空数值
- [ ] 实际效果：覆盖七类字段 + 五维，矩阵行对所有方案一致
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间可核算、区分一次性与按次
- [ ] 创新增益：硬阈值淘汰 + 权重 ±0.05 敏感性已执行，选择在扰动下仍可辩护
- [ ] 可靠体验：`inspect`/`assemble` 各跑通，`validate` 返回 valid=true，`selftest.py` 全部 PASS
- [ ] 可靠体验：重复运行同输入结果一致（脚本无随机、确定性排序）
- [ ] 异常处理：空输入/坏 JSON/坏编码/缺参数/超大文件均退出码 0 且降级不崩溃
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv/pdf、plan_comparison.md 齐备可打开
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 7. 降级策略（诚实优先）

- 空输入目录/缺 case.json → `input_error`，写合法骨架；有输入但无候选方案/材料不足 → `insufficient_evidence`，列缺口，不编造。
- 坏 JSON/坏编码 → 记录到 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录 `oversized_file_skipped`。
- 脚本算术无法匹配报价表 → `method=not_computable`、total=null，写 pending_human，不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null 或最高可行者，rationale 说明，status=partial。
- 缺软件（pdftotext 不可用）→ 记录 `pdftotext_unavailable`，改用平台 pdf-text-tool/OCR，如实标 limitations。
- 所有限制写入 `limitations`/`pending_human`，禁止把缺失软件状态或科学数据补造成事实。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect            # 第 1 步：骨架 + case_pack
python scripts/main.py extract            # 可选：PDF 抽文本到 /app/work/extracted/
python scripts/main.py assemble           # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo               # 合成示例（明确标注，非正式案例）
python scripts/selftest.py                # 鲁棒性全量自检
```

## 9. 边界

- 不做“通用大而全”的 Skill：只解决“多候选方案比较与选择”一类任务。
- 没有真实材料时输出“材料不足”，不填充示例；demo 仅供接口验证并标注为示例。
- 交付前运行 `python scripts/selftest.py`，全部 PASS 后再交付。
