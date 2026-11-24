---
name: scix-plan-evidence
description: 为科研案例生成、比较、优化并复核可排期、可执行的实验方案：把研究目标落成可检验假设与操作化变量，配置对照组、设备材料、时间成本、参数范围与风险异常；每条关键结论强制携带 locator 与逐字 quote 写入细粒度 evidence_map，并回填依据、风险与确认项到 result.json。适用于实验方案生成与优化，尤其需要对版本化候选方案做参数/量程/预算/排期算术校验。
version: 2.0.0
---

# scix-plan-evidence：实验方案生成与证据溯源复核

## 1. 角色与何时使用

本 Skill 把一个科研案例（研究目标 + 材料 + 约束 + 版本化候选方案）转成**可排期、可执行、可复核**的实验方案，并给出依据与风险。核心不是复述目标，而是把目标拆成可检验假设，把概念落成操作化变量，把每一步绑定设备/试剂/参数/时长，再对候选方案做参数、量程、预算、排期的算术校验，最后产出最终方案与确认项。

适用：实验方案生成与优化、对 2–3 个候选方案做比较裁剪、需要证据溯源的复核场景。
不适用：纯文献综述、数据分析建模、论文写作；本 Skill 不做安全/伦理审批，未确认项必须列为「确认项」而非事实。

硬性原则（贯穿全程）：
- 只依据 `/input` 下真实材料与任务说明下结论；材料没有的数值、型号、价格、批准一律不得编造。
- 每个关键结论/数值带 `locator` + 逐字 `quote`；冲突信息两处都记并标 `source_conflict`。
- 不确定写 `uncertainty` / `pending_human`；缺失写 `not_reported`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入目录 `/input/`：至少含 `case.json` 与 `task.md`；可能还有 `materials/`、PDF 论文、前瞻性资源/价格/排期合同、候选方案文件。
- 工作目录 `/app/work/`（中间产物）；最终产物目录 `/app/output/`（含 `result.json` 与 `plan.md`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`。
- PDF 用 pdftotext 读正文（环境已装）；缺软件按第 7 节降级。
- 脚本调用（相对 Skill 根目录，先 `cd /skills/weekly-skill`；本地冒烟用 `--input/--output/--work` 覆盖）：
  - `python3 scripts/main.py input_probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py selftest`（自检，用临时目录生成的合成输入，不依赖正式案例）

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：
1. `case_id`：取 case.json 的 id/case_id/challenge_id，缺失用 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构（见 `references/plan_schema_and_evidence.md`）。
3. 信封字段：`status`、`sources`、`uncertainty`、`change_log`，另附 `pending_human`、`evidence_map`、`artifacts`、`errors`、`degradations`。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 一致。本任务以 .md/.json 为可编辑格式，不强制 Office；若产 docx/xlsx 需另附 PDF/PNG 预览。

字段语义（细则见 `references/plan_schema_and_evidence.md`）：
- 六要素：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常，均落到操作定义/单位/量程/判据。
- 依据与风险：每条依据带来源定位与引用；风险含失败模式、判据、止损点、替代路径。
- 确认项：安全、伦理、设备可用性、专家确认；未确认不写成事实。
- `evidence_map`（本版强化）：每条结论一个 claim，含 `statement`、`locator`、逐字 `quote`、`source_role`、`confidence`、`conflict`、`supports`（指向 result 中的字段路径）。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | /input → `input_probe` 得 `/app/work/input_probe.json`；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 骨架落盘 | ≤120s | ≤3 | `scaffold` 写 partial result.json + plan.md 草稿。**前 3 分钟内必须完成**，此后 /app/output/result.json 任何时刻不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF 正文/合同/候选方案，抽数值与逐字片段，冲突与缺口记入 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对候选方案做参数/量程/预算/排期算术校验；写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 `validate`，按报告修结构/类型/算术/证据，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 `validate`，确认 status 诚实、sources/uncertainty 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回 result.json（写 `.tmp` 再 `os.replace`）；change_log 记 `{ts, event, detail}`；重复运行一致（固定 JSON 键序、无随机性）。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 PDF 页码）+ `quote`（原句）。
- 冲突不隐藏：同一指标两处来源不一致 → 两个都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置「前瞻性合同」时，只标「案例给定约束（非真实报价/审批）」，不得声称真实报价或安全审批。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有版本/来源定位/金标准与 change_log；plan.md 与 result.json 一致。
- [ ] 使用增益：候选方案比较与参数裁剪有据（量程/预算/排期算术），避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用；`validate` 退出码 0。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均被识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记录「无法提取 PDF 正文」，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：薄入口，分派 input_probe / scaffold / validate / selftest 子命令。
- `scripts/core_impl.py`：扫描输入、读 case.json/task.md、发现 output_contract、原子写骨架、结构/类型/算术/证据自检。
- `scripts/selftest.py`：在临时目录生成合成输入，跑通 probe→scaffold→validate 与空输入/坏 JSON 分支。
- `references/plan_schema_and_evidence.md`：字段语义、evidence_map 细粒度结构、fallback 结构。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复。

## 9. 最小调用示例（示意，非正式案例结果）

输入（示意）：
```json
{"case_id": "demo-001", "goal": "评估某抑制剂对某细胞系增殖的影响",
 "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}}
```

调用：
```bash
cd /skills/weekly-skill
python3 scripts/main.py input_probe --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# ...分析并增量填写 result.json / plan.md ...
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

示意输出 result.json（骨架，非正式案例结果）：
```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "sources": [], "uncertainty": [], "change_log":
 [{"ts": "...", "event": "skeleton_written"}]}
```
