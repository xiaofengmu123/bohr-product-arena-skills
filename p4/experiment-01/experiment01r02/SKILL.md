---
name: sci-plan-timeboxed
description: 为科研案例生成、比较、优化并复核可排期、可执行的实验方案（假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常），以“阶段预算 + 早落盘 + 截止线治理”保证任何中断都留下可用的部分产物，并回填依据、风险与确认项到结构化结果。当需要把研究目标转成实验方案、或对多个候选方案做参数/量程/预算/排期校验时使用。
version: 2.0.0
---

# sci-plan-timeboxed：时间盒式实验方案生成与优化

## 1. 角色与何时使用

本 Skill 把科研案例（研究目标 + 材料 + 约束 + 3 个版本化候选方案 + 资源/价格/排期合同）转成**可排期、可执行、可复核**的实验方案，并对候选方案做参数/量程/预算/排期的**真实算术校验**，再产出最终方案、依据与风险、确认项。适用于“实验方案生成与优化”类任务。

不适用：纯文献综述、数据分析建模、论文写作；本 Skill 不做安全/伦理审批，未经确认的内容只列为“确认项”而非事实。

硬性原则（贯穿全程）：
- 只依据 `/input` 下真实材料下结论；材料没有的数值、型号、价格、批准一律**不编造**，写 `not_reported` 或列 `pending_human`。
- 每个关键结论/数值带 `locator` + 逐字 `quote`；两处来源冲突时都记录并标 `source_conflict`。
- 材料中的“前瞻性合同”只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 2. 输入契约（运行时）

- 原始输入 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；可能还有 `materials/`、PDF 论文、候选方案文件、资源/价格/排期合同。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 `result.json` 与 `plan.md`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`。
- PDF 用 pdftotext 提取正文（缺失时降级，见第 7 节）。
- 脚本调用（相对 Skill 根目录；本地冒烟用 `--input/--output/--work` 覆盖默认路径 `/input`、`/app/output`、`/app/work`）：
  - `python3 scripts/input_probe.py --input /input --work /app/work`
  - `python3 scripts/scaffold_result.py --input /input --output /app/output --work /app/work`
  - `python3 scripts/validate_result.py --input /input --output /app/output --work /app/work`

## 3. 输出契约（必产出 /app/output/result.json 与 /app/output/plan.md）

`result.json` 必须包含：
1. `case_id`：取 case.json 的 `case_id`/`challenge_id`/`id`，仍无取 task.md 的 `challenge_id`，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构（见 references/plan_schema_and_evidence.md）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（另附 `errors`、`degradations`、`runtime` 可选）。
4. 可编辑主产物 `plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 一致；本任务以 .md/.json 为可编辑格式，若产 docx/xlsx 需另附真实 PDF/PNG 预览。

字段语义（细则见 references/plan_schema_and_evidence.md）：
- 六要素：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常，均落到操作定义/单位/量程/判据。
- 依据与风险：每条依据带来源定位与引用；风险含失败模式、判据、止损点、替代路径。
- 确认项：安全、伦理、设备可用性、专家确认；未确认不写成事实。

## 4. 执行流程：时间盒与早落盘（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

**核心取舍（本版相对普通“先分析后写盘”的差异）**：骨架不是空占位，而是**首 3 分钟内即通过结构校验的“合法 partial”**；此后每步只做增量改进，脚本按墙钟时间与阶段账本（`/app/work/budget.json`）给出截止线信号，剩余时间不足即强制走收尾路径。

| 阶段 | 时间盒 | 输入 → 输出 | 落盘动作 |
|-|-|-|-|
| 1 发现 | 0–60s | /input → `input_probe.py` 得 `/app/work/input_probe.json`；定 case_id、output_contract、材料/PDF/合同/候选方案清单 | 写 probe 报告 |
| 2 早落盘 | 60–180s | `scaffold_result.py` 写**通过结构校验的 partial** result.json + plan.md 骨架，初始化 budget 账本 | **≤3 分钟必须完成**，此后 result.json 任何时刻不得缺失/损坏 |
| 3 证据提取 | 180–420s | 读 PDF 正文/合同/候选方案，抽数值与逐字引用，冲突与缺口写入 work 笔记 | 增量回填 `sources`/`evidence_map` |
| 4 方案生成 | 420–720s | 目标→假设→变量→对照→参数→资源→判读；对 3 个候选方案做量程/预算/排期算术；写 plan.md 与 result.json | 增量改写，每改一次追加 change_log |
| 5 自检修复 | 720–840s | `validate_result.py`，按报告修结构/类型/算术/证据 | 增量更新 change_log |
| 6 收尾 | 840–900s | 重跑 validate，确认 status 诚实、sources/uncertainty 完整，写 runtime | 最终原子写回 |

硬规则：
- **早落盘**：阶段 2 的 partial 必须含全部契约键 + `status=partial` + 空六要素 + 空 sources/uncertainty，使 validate 退出码为 0（含警告）而非 1。
- **增量写回**：每次大改动原子写回（写 `.tmp` 再 `os.replace`）；`change_log` 记 `{ts, event, detail}`；重复运行内容一致（固定 JSON 键序、无随机性；ts 为墙钟元数据）。
- **截止线治理**：脚本每次运行打印 `BUDGET_STATUS`（剩余秒/请求估计）。进入阶段 4 前若 `remaining_s < 120`，跳过精修，直接 validate + 按现状诚实定 status + 写 runtime。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 PDF 文件名+页码）+ `quote`（原句），写入 `evidence_map`。
- 冲突不隐藏：两处来源不一致 → 都记录并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置“前瞻性合同”时，只标“案例给定约束（非真实报价/审批）”。

## 6. 自检清单（逐条勾选，对应评分维度；细则见 references/self_check_rubric.md）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内且 `proposed_value ∈ [allowed_min, allowed_max]`；预算 ≥ 成本合计；总时长 ≥ 单步时长；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 可核验性：result.json 有 case_id/status/change_log；证据带 locator+quote；plan.md 与 result.json 一致。
- [ ] 使用增益：3 个候选方案做量程/预算/排期算术比较并给出取舍，避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用；`selftest.py` 退出码 0。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记录“无法提取 PDF 正文”，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：统一入口 `probe|scaffold|validate|all|selftest`；默认 `all` 走最小可用路径（probe→scaffold→validate）。
- `scripts/input_probe.py`：扫描输入、读 case.json/task.md、抽取 PDF 文本、发现 output_contract，写 /app/work/input_probe.json。
- `scripts/scaffold_result.py`：回填全部契约键生成**通过校验的 partial** result.json 与 plan.md 骨架，初始化 budget 账本。
- `scripts/validate_result.py`：结构/类型/算术/六要素/证据自检 + 截止线信号；退出码 0=可用（含警告），1=必须修复。
- `scripts/selftest.py`：空输入、最小案例、坏 JSON 三态自检；退出码 0=全部通过。
- `references/plan_schema_and_evidence.md`：字段语义、证据规范、fallback 结构、status 语义。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复（可复用清单）。

## 9. 最小调用示例（示意，非正式案例结果）

输入（示意）：
```json
{"case_id": "demo-001", "goal": "评估某抑制剂对某细胞系增殖的影响",
 "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}}
```

调用：
```bash
cd /skills/weekly-skill
# 最小可用路径（发现→早落盘→校验，等价于下面三条分别执行）
python3 scripts/main.py all --input /input --output /app/output --work /app/work
# 或逐步：
python3 scripts/input_probe.py --input /input --work /app/work
python3 scripts/scaffold_result.py --input /input --output /app/output --work /app/work
# ...读取 /app/work/input_probe.json，分析并增量填写 result.json / plan.md ...
python3 scripts/validate_result.py --input /input --output /app/output --work /app/work
# 提交/交付前自检（最小案例/空输入/坏 JSON 三态）
python3 scripts/selftest.py
```

示意输出 result.json（骨架，非正式案例结果）：
```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "sources": [], "uncertainty": [], "pending_human": [],
 "change_log": [{"ts": "2026-01-01T00:00:00Z", "event": "skeleton_written"}]}
```
