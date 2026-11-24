---
name: scix-experiment-planner
description: 把科研案例（研究目标 + 材料 + 约束 + 3 个版本化候选方案）转成可排期、可执行、可复核的实验方案：从目标反推可检验假设与变量，设计对照与设备/试剂/参数范围，对候选方案做量程、预算、排期算术校验，并输出方案正文、证据台账（locator+逐字quote）、风险预案与待确认项。当需要生成、比较、优化或复核实验方案时使用。
version: 1.1.0
---

# scix-experiment-planner：实验方案生成、比较与优化

## 1. 角色与何时使用

把科研案例转成**可排期、可执行、可复核**的实验方案，并显式做候选方案比较与参数/预算/排期算术校验。输入通常含研究目标、真实科研材料（PDF）、组织方前瞻性资源/价格/排期合同、以及 **3 个版本化候选方案**；输出最终方案 + 证据台账 + 风险预案 + 待确认项。

不适用：文献综述、数据分析建模、论文写作。本 Skill 不做安全/伦理审批，未确认项只写 `pending_human`，绝不写成事实。

硬性原则（贯穿全程）：
- 只依据 `/input` 下真实材料下结论；材料没有的数值/型号/价格/批准一律 `not_reported`，不得编造。
- 每个关键结论/数值带 `locator` + 逐字 `quote`，经 `claim_id` 进证据台账；冲突信息两处都记并标 `source_conflict`。
- 组织方前瞻性合同只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。
- 不确定写 `uncertainty`，缺失写 `not_reported`，待确认写 `pending_human`；`status` 如实。

## 2. 输入契约（运行时）

- `/input/` 至少含 `case.json`（可能带 `output_contract`）与 `task.md`；通常还有 `materials/`（PDF 论文、资源/价格/排期合同）与 3 个候选方案（内嵌于 case.json 或独立文件）。
- 中间产物 `/app/work/`（唯一中间表示 `ir.json` + 抽取文本 `texts/`）；最终产物 `/app/output/`（含 `result.json` 与 `plan.md`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本 >2MB 跳过并记 `degradations`。
- PDF 用 `pdftotext -layout` 抽正文（环境已装，脚本内 `shutil.which` 探测）；缺软件按第 7 节降级。
- 脚本调用（相对 Skill 根目录，先 `cd /skills/weekly-skill`；本地冒烟用 `--input/--output/--work` 覆盖）：
  - `python3 scripts/main.py probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：
1. `case_id`：取 case.json 的 case_id/id/challenge_id，缺失用 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构（见 references/schema_and_evidence.md）。
3. 信封字段：`status`、`artifacts`（主产物 plan.md）、`sources`、`uncertainty`、`pending_human`、`change_log`（另附 `errors`、`degradations`、`runtime`）。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 同源一致。以 .md/.json 为可编辑格式，不强制 Office；若产 docx/xlsx 需另附 PDF/PNG 预览。

字段语义细则见 references/schema_and_evidence.md；六要素依次为：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常，全部落到操作定义/单位/量程/判据。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | `probe` 写 `/app/work/ir.json`：定 case_id、output_contract、材料/合同/候选方案清单与 PDF 文本 |
| 2 骨架落盘 | ≤120s | ≤3 | `scaffold` 写 partial result.json + plan.md 草稿。**前 3 分钟内必须完成**，此后 result.json 任何时刻都不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 ir.json 中 PDF 文本/合同/候选方案，抽数值与可引用片段入证据台账，冲突与缺口记 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对 3 个候选方案做量程/预算/排期算术校验并取舍；写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 `validate`，按报告修结构/类型/算术/证据，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 `validate`，确认 status 诚实、sources/uncertainty/pending_human 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回（写 `.tmp` 再 `os.replace`）；change_log 记 `{ts, event, detail}`；重复运行一致（固定 JSON 键序、无随机）。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：证据台账 `evidence_ledger` 记 `claim_id → {text, locator, quote, status}`；六要素用 `claim_ids[]` 引用。
- 冲突不隐藏：同一指标两来源不一致 → 两条都记并标 `source_conflict`，写进 `uncertainty`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置前瞻性合同时，只标“案例给定约束（非真实报价/审批）”。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有 case_id/status/sources/uncertainty/pending_human/change_log；plan.md 与 result.json 一致且含 case_id。
- [ ] 使用增益：对 3 个候选方案做量程/预算/排期算术校验与参数裁剪，避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径，至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用。
- [ ] 稳定性：空输入→insufficient_evidence 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。
- [ ] 交付前脚本自检：`python3 scripts/selftest.py` 四类冒烟全过，`validate` 退出码 0。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记“无法提取 PDF 正文”，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id + status + errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：薄入口，分派到 core_impl；`probe|scaffold|validate|all` 子命令。
- `scripts/core_impl.py`：流水线实现；以 `/app/work/ir.json` 为唯一中间表示，result.json/plan.md 由其投影派生。
- `scripts/selftest.py`：空输入/坏 JSON/缺输入/正常样例四类冒烟，退出码 0=全过。
- `references/schema_and_evidence.md`：字段语义、证据规范、fallback 结构、算术校验。
- `references/candidate_plan_audit.md`：3 个候选方案的审计模板（参数-量程/预算-报价/排期-窗口/样本量/对照/可执行性）与决策输出。
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
python3 scripts/main.py probe --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# ...读 ir.json 分析、增量填写 result.json 与 plan.md ...
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
```

示意输出 result.json（骨架，非正式案例结果）：

```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "artifacts": [{"path": "/app/output/plan.md", "role": "main_plan", "format": "markdown"}],
 "sources": [], "uncertainty": [], "pending_human": [],
 "change_log": [{"ts": "...", "event": "skeleton_written", "detail": "partial 骨架已落盘"}]}
```
