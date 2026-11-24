---
name: sci-plan-comparison
description: 在固定研究目标、资源/价格/排期约束与多个（通常 3 个）候选实验方案之间做可复核的横向比较与选择；把流程沉淀为三层结构化中间表示（case_pack → plans_model/comparison_model → result.json），执行真实算术、硬阈值淘汰、权重敏感性、证据注册表溯源，输出可执行方案、比较矩阵与 result.json。当任务要求“比较并选择/优化实验方案”时使用。
version: 2.0.0
---

# 实验方案比较与选择 Skill（中间表示与可复核版）

## 1. 角色与何时使用

把“多个候选方案 → 可比矩阵 → 可辩护选择”的科研流程沉淀为可复跑、可审计的流程。适用：给定研究目标、硬约束、材料与 3 个左右候选方案，需比较并给出选择、舍弃理由、风险与确认项。不适用：单一方案从头设计、文献综述/全文翻译、数据统计建模（仅作背景引用，不计为主要产出）。

本版核心取舍：**三层结构化中间表示（IR）**。事实、方案、比较三层分离，每层带 schema 版本、输入指纹与证据注册表，使每个中间结论都能独立复核，而非把推理藏进一次性长文。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、候选方案、约束、`output_contract`）、`/input/task.md`（题目说明）。
- 材料：`/input/` 及子目录下的 PDF/文本/CSV（原论文、报价/排期合同等）。**必须读实际文件**，不按标签臆造；PDF 用平台 `pdf-text-tool`/`pdftotext` 提取后再读原文。
- 脚本不假设固定键名：`case.json` 中的候选方案、约束、`output_contract` 均递归发现；`output_contract` 的全部键必须原样回填到 `result.json`。
- 工作目录 `/app/work`，产物目录 `/app/output`。脚本路径相对包根写为 `scripts/main.py`。

## 3. 输出契约（/app/output/result.json）

`result.json` 至少含：

- `case_id`；`status` ∈ `ok | partial | insufficient_evidence | input_error`
- 题面 `output_contract` 的**全部字段**（顶层平铺 + `output_contract` 内一份；无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组）
- `research_question`、`comparison_frame`、`weights`、`thresholds`、`elimination_log`
- `candidates[]`：统一填**七类字段**（assumptions/variables/controls/steps/parameters/resources/interpretation_criteria）+ 五维评分 + 可行性 + 成本/时间分解 + 风险 + `evidence_refs`
- `comparison_matrix`（维度×方案的归一化矩阵）、`selection`（chosen/ranking/rationale/rejected/sensitivity）
- `sources`（每条 `{id, claim, locator, quote, kind}`）、`uncertainty`、`confirmations`、`pending_human`、`change_log`（含时间戳）
- `errors`、`degradations`、`limitations`、`runtime`、`artifacts`、`intermediate_artifacts`（plans_model/comparison_model 的路径与哈希）

成本口径统一为：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工，区分一次性投入与按次消耗；时间口径同样含准备/排期/返工。数字可核算并标来源。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：第 1 步立即落盘一版合法结果骨架，之后只做增量改进。任何中途失败都不留空产物。**

| 步 | 动作 | 输入 | 输出 | 预算 |
|---|---|---|---|---|
| 1 | `python scripts/main.py inspect` | /input | `/app/output/result.json`（骨架）+ `/app/work/case_pack.json` | ≤90s / ≤3 次 |
| 2 | `python scripts/main.py init-models`，读 case_pack + 实际材料，核对候选、约束、报价 | /app/work/case_pack.json、/input | 心智模型与证据笔记 | ≤240s / ≤15 次 |
| 3 | 逐方案填 `/app/work/plans_model.json`（七类字段+五维评分+证据注册表）与 `/app/work/comparison_model.json`（框架/权重/阈值/选择） | 材料 | plans_model、comparison_model | ≤300s / ≤30 次 |
| 4 | `python scripts/main.py assemble` | 三层 IR | /app/output 全部产物 | ≤120s / ≤5 次 |
| 5 | `python scripts/main.py validate /app/output/result.json`，按第 5 节自查并修订 | result.json | 修订 | ≤90s / ≤8 次 |
| 6 | 剩余时间增量改进：补证据、复核算术、修 status | — | 更新 IR 后重跑 assemble | ≤60s / ≤3 次 |

> 第 1 步由脚本保证 3 分钟内落盘：inspect 无论输入好坏都写合法 result.json（缺 case.json → `input_error`；缺候选/材料 → `insufficient_evidence`）。第 3 步写 IR 前先保证 result.json 已存在。每层 IR 带 `schema`、`schema_version`、`input_hash`，供复跑比对。

## 5. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：所有候选填同一张七类字段矩阵，可比口径一致；选择方案并给舍弃理由
- [ ] 实际效果：每个数字/参数/结论能指回 `evidence_registry` 的 locator + 逐字 quote，无凭空数值
- [ ] 实际效果：七类字段 + 五维评分全覆盖；比较矩阵同套行
- [ ] 使用增益：机时/耗材/人力/准备/排期/返工六项成本与时间可核算，一次性与按次分开
- [ ] 创新增益：硬阈值淘汰 + 权重 ±0.05 敏感性已执行，选择在扰动下仍可辩护
- [ ] 可靠体验：`inspect`/`init-models`/`assemble`/`validate` 各跑通，validate 返回 valid=true
- [ ] 可靠体验：重复运行同输入结果一致（无随机、确定性排序）
- [ ] 异常处理：设备不可得/超预算/参数冲突/全部不可行时 status 标 partial 且列 pending_human
- [ ] 生态贡献：result.json、comparison_matrix.svg/csv、plan_comparison.md 齐备可打开；PDF 以 `%PDF-` 开头
- [ ] 生态贡献：三层 IR（case_pack/plans_model/comparison_model）与 result.json 内容一致，他人可按本文件复跑

## 6. 降级策略（诚实优先）

- 缺 case.json → `input_error`；有输入但无候选方案/材料不足 → `insufficient_evidence`，列出缺口，不编造方案或章节。
- 坏 JSON/坏编码 → 记录 errors/degradations，继续处理其余材料；超 2MB 单文件跳过并记录。
- 算术无法匹配报价表 → cost.method=`not_computable`，total=null，写 pending_human；不强行给数。
- 设备档期/安全伦理未确认 → confirmations 标 pending，不写成可执行事实。
- 候选全部不可行 → 不强行选择；chosen=null 或最高可行者，rationale 说明，status=partial。
- 所有限制写入 `limitations`/`pending_human`/`degradations`；缺软件状态或科学数据不得补造成事实。

## 7. 脚本调用（相对路径，标准库 only）

```bash
python scripts/main.py --help
python scripts/main.py inspect          # 第 1 步：骨架 + case_pack
python scripts/main.py init-models      # 第 2 步：IR 骨架（plans_model/comparison_model）
python scripts/main.py assemble         # 第 4 步：result.json + 矩阵/文档/PDF
python scripts/main.py validate /app/output/result.json
python scripts/main.py demo             # 示例（标注“示例”，非正式案例）
python scripts/selftest.py              # 交付前自检
```

可用 `--input/--work/--output` 或环境变量 `SKILL_INPUT_DIR`/`SKILL_WORK_DIR`/`SKILL_OUTPUT_DIR` 覆盖路径。

## 8. 证据溯源（科研硬要求）

每个关键结论/数值/参数范围必须写入 `plans_model.evidence_registry`（`{id, claim, locator, quote, kind}`），并在方案字段、风险、成本行中通过 `evidence_refs`/`evidence_ref` 引用其 id。`assemble` 会把注册表转为 `result.json.sources`；`validate` 检查引用是否可解析、是否存在无引用的孤儿证据。冲突信息不隐藏：同一指标来源不一致时两处都记并标 `source_conflict`。不虚构引用/DOI/数值；缺失写 `pending_human` 或 `not_reported`。

## 9. 中间表示与可复核（本版侧重点）

- **case_pack.json**（事实层）：约束、候选、材料索引、output_contract、输入指纹 `input_hash`。
- **plans_model.json**（方案层）：逐方案七类字段、五维评分、成本/时间行、风险、证据引用。
- **comparison_model.json**（比较层）：比较框架、权重、硬阈值规则、选择、确认项、不确定性。
- 三层 IR 均落 `/app/output`，与 result.json 同目录，供独立审查；`result.json.intermediate_artifacts` 记录其路径与 SHA256，供复跑比对。
- 一致性：result.json 的 candidates/sources/selection 由 IR 确定性派生；改结论必须先改 IR 再 assemble，不直接改 result.json。
