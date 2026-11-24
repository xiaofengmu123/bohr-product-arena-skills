---
name: sci-plan-arbiter
description: 为给定科研案例（研究目标、真实材料、约束、版本化候选方案与前瞻性资源/报价/排期合同）生成、比较、算术校验并复核可排期、可执行的实验方案，回填 output_contract 全部字段、证据定位、风险与确认项到 result.json。当需要把研究目标转成可检验假设与可执行方案，或对 2–3 个候选方案做参数/量程/预算/排期算术校验与选择时使用。
version: 2.0.0
---

# sci-plan-arbiter：实验方案生成、比较与算术校验

## 1. 角色与何时使用

本 Skill 把一个科研案例（目标 + 材料 + 约束 + 候选方案 + 前瞻性合同）转成**可排期、可执行、可复核**的实验方案：目标→可检验假设→操作变量→对照→步骤→参数→资源→判读标准，并对每个案例的 3 个版本化候选方案做参数/量程/预算/排期算术校验后给出选择与修订。适用于“实验方案生成与优化”，尤其匹配 generate_and_revise_plan 案例（真实文献 + 显式资源/报价/排期合同 + 每案例 3 个候选方案 + 有限参数域 + 需算术校验）。

不适用：文献综述、数据分析建模、论文写作；不替代安全/伦理审批。未确认项一律列为“确认项/待办”，不写成事实。

硬性原则（贯穿全程）：只依据 `/input` 下真实材料与任务说明下结论；每个关键结论/数值带 `locator` + 逐字 `quote`；冲突信息两处都记并标 `source_conflict`；不确定写 `uncertainty`/`pending_human`；缺失写 `not_reported`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；还可能有 `materials/`、PDF 论文、`resources/`（价格/报价/排期合同）、候选方案文件（`candidate_plan_*.md` 或 case.json 内嵌 plans 数组）。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（必须含 `result.json`，可编辑主产物 `plan.md`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`；PDF 用 pdftotext 提取正文（缺软件走第 7 节降级）。
- 脚本（相对 Skill 根目录执行，路径写 `scripts/` 前缀；本地冒烟用 `--input/--output/--work` 覆盖默认路径）：
  - `python3 scripts/main.py probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`
  - `python3 scripts/selftest.py`（自检，在 Skill 根目录运行）

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：

1. `case_id`：取 case.json 的 case_id/challenge_id，缺失取 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构（见 `references/schema_and_checks.md` §3）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`；另附 `errors`、`degradations`、`runtime` 可选。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据风险 + 确认项 + 候选比较 + 算术校验），与 result.json 一致。

无契约时的 fallback 顶层键：`plan`、`evidence_and_risks`、`confirmations`、`candidate_comparison`、`arithmetic_validation`、`selected_plan`，再加信封字段。六要素映射：假设与变量→`plan.hypotheses`/`plan.variables`；对照组→`plan.controls`；设备材料→`plan.resources`；时间成本→`plan.steps` 的时长与 `plan.time_cost.budget`；参数范围→`plan.parameters`；风险与异常→`evidence_and_risks.risks`。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | 跑 `main.py probe` → `/app/work/input_probe.json`；定 case_id、output_contract、材料/PDF/合同/候选方案/参数域清单 |
| 2 骨架落盘 | ≤120s | ≤3 | 跑 `main.py scaffold` → partial result.json（case_id + 契约全键 + 信封）+ plan.md 骨架。**必须在开始后 3 分钟内落盘**；此后 result.json 任何时刻不得缺失/损坏 |
| 3 证据与候选提取 | ≤240s | ≤12 | 读 PDF 正文/合同/3 个候选方案，抽数值与可引用片段，冲突与缺口写入 work 笔记 |
| 4 方案生成与仲裁 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读；对候选做参数/量程/预算/排期算术校验，选出或修订方案，增量写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 `main.py validate`，按报告修结构/类型/算术/证据，增量记 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 validate 至退出码 0，确认 status 诚实、sources/uncertainty/pending_human 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回 result.json（写 `.tmp` 再 `os.replace`）；change_log 记 `{ts, event, detail}`；重复运行内容一致（固定 JSON 键序、无随机性，仅 `change_log.ts` 与 `runtime` 为时间量）。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 `materials/paper.pdf#p5`）+ `quote`（原句逐字）。
- 冲突不隐藏：同一指标两处来源不一致 → 都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置“前瞻性合同”时，只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 6. 自检清单（提交/交付前逐条勾选）

- [ ] 完成度：假设可检验（非复述目标）；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围+来源，步骤绑定设备/试剂/时长/成本）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照写明排除哪类混杂；参数在设备/试剂允许区间；引用与规格真实；算术（总价=Σ单价×数量、总时长=关键路径）复核通过。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有 case_id/status/change_log；plan.md 与 result.json 一致；每条依据有 locator+quote。
- [ ] 使用增益：3 个候选方案有逐项比较（量程/预算/排期算术）并给出选择理由，非模板空话。
- [ ] 创新增益：至少落实一类——按研究目标反推对照、按量程+预算裁剪参数、生成风险预案与替代路径。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断结果是否可用。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定→识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 `references/`）。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记“无法提取 PDF 正文”，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：子命令 `probe`/`scaffold`/`validate`（无子命令=probe+scaffold 先落盘）；纯标准库、确定性、原子写、UTF-8-sig 容错、>2MB 跳过、pdftotext 降级。
- `scripts/selftest.py`：构造最小 demo 输入（含空输入、坏 JSON 两类）验证契约与退出码，全部通过打印 PASS。
- `references/schema_and_checks.md`：字段语义、fallback 结构、算术约定、六要素检查项。
- `references/rubric_mapping.md`：评分维度→流程步骤→自检项→常见失败与修复。

## 9. 最小调用示例（示意，非正式案例结果）

输入（示意，明确标记为示例）：

```json
{"case_id":"demo-001","goal":"评估某抑制剂对某细胞系增殖的影响",
 "output_contract":{"plan":{},"evidence_and_risks":{},"confirmations":{}}}
```

调用：

```bash
cd /skills/weekly-skill
python3 scripts/main.py probe --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# ……分析并增量填写 result.json / plan.md ……
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

示意输出 result.json（骨架，非正式案例结果）：

```json
{"case_id":"demo-001","plan":{},"evidence_and_risks":{},"confirmations":{},
 "status":"partial","sources":[],"uncertainty":[],"pending_human":[],
 "change_log":[{"ts":"...","event":"skeleton_written"}]}
```
