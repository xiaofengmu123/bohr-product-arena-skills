---
name: scix-experiment-plan-v2
description: 为科研案例生成、比较、优化并复核可排期、可执行的实验方案（假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常），并回填依据、风险与确认项到结构化结果。当需要把研究目标转成实验方案，或对 2-3 个版本化候选方案做参数/量程/预算/排期算术校验时使用。本版以“自检与确定性”为主线：scripts/selftest.py 与可勾选清单保证同输入两次产物逐字节一致。
version: 2.0.0
---

# scix-experiment-plan-v2：实验方案生成与优化（自检与确定性版）

## 1. 角色与何时使用

把科研案例（研究目标 + 材料 + 约束 + 候选方案）转成**可排期、可执行、可复核**的实验方案，说明依据与风险，并对版本化候选方案做参数/量程/预算/排期算术校验后择优。适用于“实验方案生成与优化”。

不适用：纯文献综述、数据分析建模、论文写作；不做安全/伦理审批，未确认项一律列入“确认项”而非事实。

硬性原则（贯穿全程）：只依据 `/input` 真实材料下结论；每个关键结论/数值带 `locator` + 逐字 `quote`；冲突两处都记并标 `source_conflict`；缺失写 `not_reported` / `pending_human`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；可能还有 `materials/`、PDF 论文、前瞻性资源/价格/排期合同、候选方案文件。
- 工作目录 `/app/work/`（中间产物）；最终产物目录 `/app/output/`（含 `result.json`）。
- 不假设固定文件名/键名：先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文件 >2MB 跳过并记 `degradations`。
- PDF 用 `pdftotext -layout` 抽正文（本环境已装）；缺工具按第 6 节降级。
- 脚本调用（相对 Skill 根目录；先 `cd /skills/weekly-skill` 或直接用绝对前缀）：

```bash
python3 scripts/main.py --input /input --work /app/work --stage probe
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage scaffold
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage validate
python3 scripts/selftest.py
```

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：

1. `case_id`：取 case.json，缺失用 task.md 的 `challenge_id`，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用回退结构（见 `references/plan_schema_and_evidence.md`）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（另附 `errors`、`degradations`、`determinism`、`skill`）。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 一致。文本类给 `.md/.json`；若产 docx/xlsx 需另附 PDF/PNG 预览。

`change_log.ts` 只允许逻辑步号 `L0001`、`L0002`…，**禁用墙钟时间**（保证确定性）。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | /input → `probe` 得 work/input_probe.json；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 骨架落盘 | ≤120s | ≤3 | `scaffold` 写 partial result.json + plan.md 草稿。**前 3 分钟内必须完成**，此后 result.json 任何时刻不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF 正文/合同/候选方案，抽数值与可引用片段，冲突与缺口记入 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对候选方案做参数/量程/预算/排期算术校验；写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 `validate`，按报告修结构/类型/算术/证据，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 `validate`，确认 status 诚实、sources/uncertainty 完整 |

硬规则：先落盘再分析；每次大改动原子写回（脚本已用 `.tmp`+`os.replace`）；重复运行一致（sort_keys、逻辑步号、无随机性）。同输入两次运行，result.json 与 plan.md 必须逐字节一致。

## 5. 自检清单（逐条勾选，对应评分维度，细则见 references/self_check_rubric.md）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程、对照有目的、参数有范围与来源、步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有版本/来源定位/金标准与 change_log；plan.md 与 result.json 一致。
- [ ] 使用增益：候选方案比较与参数裁剪有据（量程/预算/排期算术），避免模板式空话；不伪造增益评测。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用；`--help` 退出码 0。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致（selftest.py 已机械验证）。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均被识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 6. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记录“无法提取 PDF 正文”，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），不崩溃（脚本已兜底）。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 7. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 PDF 页码）+ `quote`（原句）。
- 冲突不隐藏：两处来源不一致 → 都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置“前瞻性合同”时，只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 8. 脚本与资源

- `scripts/main.py`：输入发现（probe）、结果骨架落盘（scaffold）、结构/类型/算术/证据/确定性自检（validate）。纯标准库。
- `scripts/selftest.py`：把第 5 节清单机器化 + 两次运行逐字节一致性验证。纯标准库。
- `references/plan_schema_and_evidence.md`：字段语义、证据规范、回退结构。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复。

## 9. 最小调用示例（示意，非正式案例结果）

输入（示意，正式案例由平台提供）：

```json
{"case_id": "demo-001", "goal": "评估某抑制剂对某细胞系增殖的影响",
 "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}}
```

调用：

```bash
cd /skills/weekly-skill
python3 scripts/main.py --input /input --output /app/output --work /app/work
python3 scripts/main.py --input /input --output /app/output --work /app/work --stage validate
python3 scripts/selftest.py
```

示意输出 result.json（骨架，非正式案例结果）：

```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "sources": [], "uncertainty": [], "pending_human": [],
 "change_log": [{"ts": "L0001", "event": "skeleton_written"}]}
```
