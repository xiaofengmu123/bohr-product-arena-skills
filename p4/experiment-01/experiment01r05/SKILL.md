---
name: experiment-plan-optimizer
description: 把科研案例（研究目标 + 材料 + 约束 + 候选方案）转成可排期、可执行、可复核的实验方案：拆可检验假设与操作化变量、按混杂设计对照、绑定设备/试剂/参数/预算、做参数与排期算术校验、生成风险预案，并把依据（locator + 逐字 quote）与确认项回填到结构化结果。当需要生成、比较、优化实验方案，或对候选方案做参数/量程/预算/排期校验时使用。
version: 1.1.0
---

# experiment-plan-optimizer：实验方案生成与优化

## 1. 角色与何时使用

本 Skill 把固定科研案例（研究目标 + 科学材料 + 约束 + 候选方案）转成一份**可排期、可执行、可复核**的实验方案，并说明依据与风险。适用于"实验方案生成与优化"，尤其需要对多个候选方案做参数/量程/预算/排期算术校验，再产出最终方案与确认项的场景。

不适用：文献综述、数据分析建模、论文写作；本 Skill 不做安全/伦理审批，未确认项只写为"确认项"而非事实。

硬性原则（贯穿全程）：
- 只依据 `/input` 下真实材料与任务说明下结论；材料没有的数值、型号、价格、批准一律不得编造。
- 每个关键结论/数值带 `locator` + 逐字 `quote`；冲突信息两处都记并标 `source_conflict`。
- 不确定写 `uncertainty` / `pending_human`；缺失写 `not_reported`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入目录 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；可能有 `materials/`、PDF 论文、资源/价格/排期合同、候选方案文件。
- 工作目录 `/app/work/`（中间产物）；最终产物目录 `/app/output/`（含 `result.json`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`。
- PDF 用 pdftotext 读正文；缺软件时按第 7 节降级。
- 脚本调用（相对 Skill 根目录，先 `cd /skills/weekly-skill`；本地冒烟用 `--input/--output/--work` 覆盖路径）：
  - `python3 scripts/main.py probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py patch --output /app/output --patch /app/work/patch.json`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`
  - `python3 scripts/selftest.py`（交付前自检）

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：

1. `case_id`：取 case.json 的 `case_id`，缺失用 task.md 的 `challenge_id`，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构（见 `references/schema_and_evidence.md`）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（另附 `errors`、`degradations`、`runtime`、`contract_coverage`）。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 一致。

**契约对齐（本版重点）**：`scaffold` 把 `output_contract` 的键路径树逐层物化为合法 JSON（空对象/空数组/null 占位），并把每个键路径写入 `contract_coverage`（`filled` / `empty`）；`validate` 重算覆盖度并比对"契约键是否缺失"。任何契约键不得改名、删除或漏写；未知键原样保留。

字段语义（六要素 + 依据/风险/确认项）见 `references/schema_and_evidence.md`。信封字段语义：
- `status ∈ {ok, partial, insufficient_evidence, input_error}`；材料不足即 `insufficient_evidence`，绝不编造。
- `sources`：来源清单（文件名/页码/类型）；`uncertainty`：不确定项；`pending_human`：待人工确认项。
- `change_log`：`[{ts, event, detail}]`，每次大改动原子写回并追加记录。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | `/input` → `main.py probe` 得 `/app/work/input_probe.json`；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 契约物化 + 骨架落盘 | ≤120s | ≤3 | `main.py scaffold` 写 partial result.json（契约全键回填）+ plan.md 草稿。**前 3 分钟内必须完成**，此后 `/app/output/result.json` 任何时刻都不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF 正文/合同/候选方案，抽数值与可引用片段，冲突与缺口记入 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对候选方案做参数/量程/预算/排期算术校验；用 `main.py patch` 增量写回 |
| 5 自检修复 | ≤180s | ≤15 | 跑 `main.py validate`，按报告修结构/类型/算术/证据，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 `validate`，确认 status 诚实、sources/uncertainty 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回（写 `.tmp` 再 `os.replace`）；`change_log` 记 `{ts, event, detail}`；重复运行一致（固定 JSON 键序、无随机性，仅 ts/runtime 允许变化）。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 PDF 页码）+ `quote`（原句）。
- 冲突不隐藏：两处来源不一致 → 都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置"前瞻性合同"时，只标"案例给定约束（非真实报价/审批）"，不得声称真实报价或安全审批。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有 case_id/status/sources/uncertainty/change_log；`contract_coverage` 全 `filled` 或已说明缺口；plan.md 与 result.json 一致。
- [ ] 使用增益：候选方案比较与参数裁剪有据（量程/预算/排期算术），避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均被识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记录"无法提取 PDF 正文"，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（`case_id`+`status`+`errors`），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：CLI 入口（薄封装，实现见 core_impl.py），子命令 `probe` / `scaffold` / `patch` / `validate` / `run`。
- `scripts/core_impl.py`：输入扫描、契约物化、骨架落盘、patch 合并、结构与算术自检。
- `scripts/selftest.py`：对包自身做空输入/坏 JSON/重复运行/算术自检。
- `references/schema_and_evidence.md`：字段语义、证据规范、fallback 结构。
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
# ...分析并增量 patch ...
python3 scripts/main.py patch --output /app/output --patch /app/work/patch.json
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

示意输出 result.json（骨架，非正式案例结果）：
```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "sources": [], "uncertainty": [], "pending_human": [],
 "contract_coverage": {"plan": "empty", "evidence_and_risks": "empty", "confirmations": "empty"},
 "change_log": [{"ts": "…", "event": "skeleton_written", "detail": "…"}]}
```
