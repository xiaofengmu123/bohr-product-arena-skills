---
name: sci-plan-ledger
description: 在固定研究目标、资源/价格/排期硬约束与多个（通常 3 个）带版本的候选实验方案之间做可复核的横向比较与选择。统一七类字段与五维评分口径，执行真实成本/时间算术、硬阈值淘汰、权重敏感性分析与 locator+逐字 quote 证据溯源，输出可执行方案、比较矩阵与 result.json；并遵循 900 秒/64 请求阶段预算，前 3 分钟先落盘 partial 结果骨架、之后只做增量改进。当任务要求“比较并选择/优化实验方案”时使用。
version: 2.0.0
---

# 实验方案比较与选择 Skill（时间预算与早落盘版）

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”沉淀为可复跑流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需要比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从头设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

本版设计取舍：把 900 s / 64 次请求拆成阶段预算，`inspect` 在前 3 分钟内先落盘一版合法 partial 骨架，之后 assemble/validate 只做增量改进；预算账本写入 `/app/work/budget_ledger.json`，可用 `progress` 查看。

核心失败模式：各方案各写一套结构、不核对设备/预算/货期、时间只算执行不算准备与返工、成本漏机时与人力、风险无触发条件与后果量级、参数无来源、结论强度靠语气。本 Skill 强制统一口径、挂证据、做算术、先落盘。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、可能的 output_contract）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下 PDF/文本/CSV（原论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造内容；PDF 用平台 pdf-text-tool / pdftotext 提取后再读。
- 脚本递归发现字段，不假设固定键名；`case.json` 中若有 `output_contract`，其全部键必须原样回填到 result.json。
- 工作目录 `/app/work`，产物目录 `/app/output`；脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

至少包含：`case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`；题面 `output_contract` 的**全部字段**（无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）；`sources`（每条 `{locator, quote, kind}`）、`uncertainty`、`change_log`（含时间戳的增量记录）；另有 `research_question`、`comparison_frame`、`candidates[]`（统一七类字段 + 成本/时间分解 + 可行性 + 风险 + 证据 + 五维评分）、`comparison_matrix`、`selection`（chosen/ranking/rationale/rejected/sensitivity）、`confirmations`、`errors`、`degradations`、`pending_human`、`runtime`、`artifacts`。

成本口径：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工六项，区分一次性投入与按次消耗；时间口径含准备/排期/执行/表征排队/返工。数字须可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步（前 3 分钟）立即落盘一版合法结果骨架，之后只做增量改进。** 任何中途失败都不留空产物。

| 步 | 动作 | 输入 | 输出 | 时间/请求 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | /app/output/result.json 骨架 + /app/work/case_pack.json + budget_ledger.json | ≤180s / ≤3 次 |
| 2 | 读 case_pack + 实际材料，核对候选方案、约束、报价/排期 | case_pack、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 写 /app/work/analysis.json（见 references/analysis_schema.md） | 材料 | analysis.json | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble` | case_pack + analysis | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 5 节自查修订 | result.json | 修订 | ≤60s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 analysis.json 后重跑 assemble | ≤60s / ≤3 次 |

第 1 步由脚本保证在 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（坏输入标 input_error，缺材料标 insufficient_evidence）。第 3 步写 analysis.json 前，先确认 result.json 已存在。随时可 `python scripts/main.py progress` 查看阶段账本。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选方案填同一张七类字段矩阵，口径一致；选出方案并给出舍弃理由
- [ ] 实际效果：比较中每个数字/参数范围/结论都能指回 locator+quote，无凭空数值
- [ ] 实际效果：覆盖 假设/变量/对照/步骤/参数/资源/判读标准 七类字段 + 目标/风险/成本/可行性/证据 五维
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间可核算，区分一次性与按次
- [ ] 创新增益：硬阈值淘汰 + 权重敏感性已执行，选择在扰动下仍可辩护
- [ ] 可靠体验：inspect 与 assemble 各跑通，validate 返回 valid=true；`selftest.py` 通过
- [ ] 可靠体验：重复运行同输入稳定核心一致（脚本无随机、确定性排序）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 如实标 partial 且列 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv、plan_comparison.md 齐备可打开；PDF 以 `%PDF-` 开头
- [ ] 生态贡献：SKILL.md 与脚本版本一致，他人可按本文件独立复跑

## 6. 降级策略（诚实优先）

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造章节或方案。
- 坏 JSON/坏编码 → 记 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 脚本算术无法匹配报价表 → cost/time.method=`not_computable`，total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null，rationale 说明，status=partial。
- 所有限制写入 limitations/pending_human；禁止把缺失软件状态或科学数据补造成事实。

## 7. 脚本调用（相对路径，标准库 only）

以下命令均在 Skill 包根目录执行（脚本内部按自身绝对路径定位实现文件，故对 cwd 不敏感）：

```bash
python scripts/main.py --help
python scripts/main.py inspect             # 第 1 步：骨架 + case_pack + 预算账本
python scripts/main.py progress            # 查看 900s/64 请求阶段预算
python scripts/main.py assemble            # 第 4 步：最终 result.json + 矩阵
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo                # 示例（标注 EXAMPLE，非正式案例）
python scripts/selftest.py                 # 完整自检
```
