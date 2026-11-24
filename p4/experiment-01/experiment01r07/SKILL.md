---
name: experiment-plan-optimizer
description: 为科研案例生成、比较、优化并复核可排期、可执行的实验方案（假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常），对候选方案做参数量程/预算/排期算术校验，并回填依据（locator+逐字 quote）、风险与确认项。当需要把研究目标转成实验方案、或对多个候选方案做参数/预算/对照校验时使用。
version: 1.0.0
---

# experiment-plan-optimizer：实验方案生成、比较与优化

## 1. 角色与何时使用

本 Skill 把科研案例（研究目标 + 科学材料 + 资源/价格/排期合同 + 候选方案）转成**可排期、可执行、可复核**的实验方案，并对 2–3 个版本化候选方案做参数量程、预算与排期算术校验后给出最终方案与确认项。

适用：把研究目标落成可检验假设与操作变量、设计对照、绑定设备/试剂/参数范围、估算时间与预算、预设风险与异常；比较与修订多个候选方案。

不适用：纯文献综述、数据分析建模、论文写作；本 Skill 不做安全/伦理审批。

硬性原则（贯穿全程）：
- 只依据 `/input` 下真实材料与任务说明下结论；材料没有的数值、型号、价格、批准一律不得编造。
- 每个关键结论/数值带 `locator` + 逐字 `quote`；冲突信息两处都记并标 `source_conflict`。
- 不确定写 `uncertainty` / `pending_human`；缺失写 `not_reported`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入目录 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；常见还有 `materials/`、PDF 论文、资源/价格/排期合同、3 份版本化候选方案。
- 工作目录 `/app/work/`（中间产物）；最终产物目录 `/app/output/`（含 `result.json`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`。
- PDF 用 `pdftotext -layout` 提取正文（本环境已装）；缺软件时按第 8 节降级。
- 脚本调用（先 `cd /skills/weekly-skill` 或直接用绝对前缀；本地冒烟可用 `--input/--output/--work` 覆盖路径）：
  - `python3 scripts/main.py probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`
  - `python3 scripts/selftest.py`

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：
1. `case_id`：取 case.json，缺失用 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有契约时用 fallback 结构（见 references/plan_schema_and_evidence.md）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`（另附 `errors`、`degradations`、`runtime` 可选）。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项），与 result.json 一致。

字段语义（细则见 references/plan_schema_and_evidence.md）：
- 六要素：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常，均落到操作定义/单位/量程/判据。
- 依据与风险：每条依据带来源定位与引用；风险含失败模式、判据、止损点、替代路径。
- 确认项：安全、伦理、设备可用性、专家确认；未确认不写成事实。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | /input → 跑 probe 得 /app/work/input_probe.json；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 骨架落盘 | ≤120s | ≤3 | scaffold 写 partial result.json + plan.md 草稿。**前 3 分钟内必须完成**，此后 /app/output/result.json 任何时刻都不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF 正文/合同/候选方案，抽数值与可引用片段，冲突与缺口记入 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对候选方案做参数/量程/预算/排期算术校验；写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 validate，按报告修结构/类型/算术/证据，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 validate，确认 status 诚实、sources/uncertainty 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回 result.json（写 `.tmp` 再 `os.replace`）；change_log 记 `{ts, event, detail}`；重复运行一致（固定 JSON 键序、无随机性）。

## 5. 六要素生成规范

- **假设与变量**：目标写成可检验“若…则…”；自变量/因变量/混杂各给操作定义、测量方式、单位、量程。
- **对照组**：空白/阴性/阳性/剂量梯度组合，逐条写 `group`、`purpose`、`confounder_controlled`。
- **设备材料**：绑定型号/量程/规格/试剂信息，标可替代与不可替代项。
- **时间成本**：步骤时长、并行关系、样本量、预算估算；总时长/总成本与步骤求和一致。
- **参数范围**：关键参数/单位/允许区间/超出区间处置，标明依据来自手册、预实验还是文献。
- **风险与异常**：失败模式（污染/无信号/重复性差/样本不足）、判据、止损点、替代路径。
- **候选比较**：对每份候选方案套用参数/量程/预算/排期算术校验，按“假设可检验 + 对照排除混杂 + 参数在量程内 + 预算排期可行”择优或合并修订。

## 6. 证据溯源与诚实性

- 关键结论/数值可回溯到材料逐字片段：`locator`（如 `materials/paper.md#p3` 或 PDF 页码）+ `quote`（原句）。
- 冲突不隐藏：两处来源不一致 → 都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或列 `pending_human`。
- 预算/排期/参数来自案例内置“前瞻性合同”时，只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果·任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，步骤绑定设备/试剂/时长）。
- [ ] 实际效果·正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 实际效果·完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险与异常 6 项均非空。
- [ ] 实际效果·可核验性：result.json 有版本/来源定位/金标准与 change_log；plan.md 与 result.json 一致。
- [ ] 使用增益：候选方案比较与参数裁剪有据（量程/预算/排期算术），避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 可靠体验·易用性：按第 2/3/4 节可完成一次生成并判断可用。
- [ ] 可靠体验·稳定性：空输入→insufficient_evidence 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 可靠体验·异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均被识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 8. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human`。
- 缺软件（pdftotext 不可用）：`degradations` 记录“无法提取 PDF 正文”，改用材料文本字段；仍无法判断则如实标注。
- 坏 JSON/坏编码：读入失败记 `errors`，继续处理其余材料；不得中断。
- 超大文件：>2MB 跳过并记 `degradations`。
- 约束冲突：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 9. 脚本与资源

- `scripts/main.py`：薄入口（probe/scaffold/validate 子命令；不带子命令则 probe→scaffold）。
- `scripts/core_impl.py`：实现（输入发现、契约回填、原子写、算术与证据校验）。
- `scripts/selftest.py`：故障注入自检（空输入/坏 JSON/超大文件/越界参数/契约回填/确定性），退出码 0=通过。
- `references/plan_schema_and_evidence.md`：字段语义、六要素细则、证据规范、算术校验规则、fallback 结构。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复。

## 10. 最小调用示例（示意，非正式案例结果）

```bash
cd /skills/weekly-skill
python3 scripts/main.py probe --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# …… 增量填写 result.json / plan.md ……
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

示意 result.json（骨架，非正式案例结果）：

```json
{"case_id": "demo-001",
 "plan": {"hypothesis_and_variables": {}, "controls": [],
          "equipment_materials": [], "time_cost": {},
          "parameter_ranges": [], "risks_and_exceptions": []},
 "evidence_and_risks": {"evidence": [], "risks": [], "source_conflicts": []},
 "confirmations": [],
 "status": "partial", "sources": [], "uncertainty": [],
 "change_log": [{"ts": "…", "event": "skeleton_written"}]}
```
