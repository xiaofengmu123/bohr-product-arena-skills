---
name: scix-experiment-plan-uncertainty
description: 为科研案例生成、比较、优化并复核可排期、可执行的实验方案（假设与变量、对照、设备材料、时间成本、参数范围、风险与异常），对每条依据做 locator + 逐字 quote 溯源，并把 limitations / uncertainty / pending_human 显式化为信封字段，缺数据绝不编造。当需要把研究目标转成实验方案，或对 2–3 个版本化候选方案做参数/量程/预算/排期算术校验并诚实标注不确定性与缺口时使用。
version: 1.0.0
---

# scix-experiment-plan-uncertainty：实验方案生成、优化与不确定性显式化

## 1. 角色与何时使用

本 Skill 把研究目标 + 材料 + 约束 + 候选方案转成**可排期、可执行、可复核**的实验方案：目标→可检验假设→操作化变量→对照→参数→资源→判读标准，并回填依据、风险与确认项。核心设计取舍是**把"不知道"当作一等公民**：每条结论要么带证据（locator + 逐字 quote），要么显式标注 assumption / not_reported / pending_human，绝不默许无来源数值、型号、价格、DOI 或引用混入结果。

适用于：实验方案生成与优化；2–3 个版本化候选方案的比较、参数裁剪与算术校验；把不确定性与缺口显式化。不适用于纯文献综述、数据分析建模、论文写作；本 Skill 不做安全/伦理审批，未确认项只列 pending_human，不写成可执行事实。

硬性原则（贯穿全程）：
- 只依据 `/input` 真实材料与任务说明下结论；材料没有的数值、型号、价格、批准一律不得编造。
- 关键结论/数值带 `locator` + 逐字 `quote`；冲突信息两处都记并标 `source_conflict`。
- 不确定写 `uncertainty`；缺失写 `not_reported`；需要人确认写 `pending_human`；`status` 如实。

## 2. 输入契约（运行时）

- 原始输入 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；可能还有 `materials/`、PDF 论文、资源/价格/排期合同、候选方案文件。
- 工作目录 `/app/work/`（中间产物）；最终产物目录 `/app/output/`（含 `result.json` 与 `plan.md`）。
- 不假设固定文件名/键名，先扫描再判断；`case.json` 用 `utf-8-sig` 容错读；单文本文件 >2MB 跳过并记 `degradations`。
- PDF 用 pdftotext 提取正文（环境已装）；缺软件按第 7 节降级，绝不伪造 PDF 正文。
- 脚本调用（相对 Skill 根目录；本地冒烟可用 `--input/--output/--work` 覆盖路径）：
  - `python3 scripts/main.py probe --input /input --work /app/work`
  - `python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work`
  - `python3 scripts/main.py validate --input /input --output /app/output --work /app/work`
  - `python3 scripts/selftest.py`

## 3. 输出契约（必产出 /app/output/result.json）

`result.json` 必须包含：
1. `case_id`：取 case.json，缺失用 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：case.json 有契约就逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）；没有就用 fallback 结构 `plan` / `evidence_and_risks` / `confirmations`（六要素见 references/plan_schema_and_evidence.md）。
3. 信封字段：`status`、`sources`、`uncertainty`、`pending_human`、`limitations`、`change_log`（另附 `errors`、`degradations`、`runtime` 可选）。
4. 可编辑主产物 `/app/output/plan.md`：人类可读完整方案（六要素 + 依据与风险 + 确认项 + 显式不确定性与限制），与 result.json 一致。本任务以 .md/.json 为可编辑格式；若产 docx/xlsx 需另附 PDF/PNG 预览。

字段语义（细则见 references/plan_schema_and_evidence.md）：
- 六要素均落到操作定义/单位/量程/判据/依据；变量须区分自变量、因变量与混杂因素。
- 依据与风险：每条依据带 `source_id` + `locator` + 逐字 `quote`；风险含失败模式、判据、止损点、替代路径。
- 确认项：安全、伦理、设备可用性、专家确认；未确认不写成事实。
- `uncertainty` 项：`{id, statement, kind, affects, severity, resolution, pending_human}`；`limitations` 项：`{id, scope, limit, reason}`。二者必须显式，缺失数据写 `not_reported` 而不填空值冒充。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | /input → 跑 probe 得 /app/work/input_probe.json；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 骨架落盘 | ≤120s | ≤3 | 跑 scaffold 写 partial result.json + plan.md 草稿。**前 3 分钟内必须完成**，此后 /app/output/result.json 任何时刻不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF 正文/合同/候选方案，抽数值与可引用片段；冲突与缺口记入 work 笔记 |
| 4 方案生成 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读标准；对候选方案做参数/量程/预算/排期算术校验；写 plan.md 与 result.json |
| 5 自检修复 | ≤180s | ≤15 | 跑 validate，按报告修结构/类型/算术/证据/无来源声明，增量更新 change_log |
| 6 收尾 | ≤60s | ≤5 | 重跑 validate，确认 status 诚实、sources/uncertainty/limitations 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回 result.json（写 `.tmp` 再 `os.replace`）；change_log 记 `{seq, event, detail}`；重复运行一致（固定 JSON 键序、无随机数、内容不含墙钟时间戳）。

## 5. 证据溯源与"不编造"闸门

- 关键结论/数值可回溯：`locator`（如 `materials/paper.md#p3` 或 PDF 页码）+ `quote`（原句）。
- 每个"声明项"必填 `basis` 标记，取值限于：`case_given`（案例给定约束，非真实报价/审批）、`computed`（由案例数值算术推得）、`assumption`（显式假设）、`not_reported`（材料未报）、`pending_human`（需确认）、`literature` / `device_manual`（须另附 locator/source_id）。
- 无 `basis` 的数值/型号/价格/引用 = 未证实声明，validate 判为 error，必须补 basis 或改写为 not_reported/pending_human。
- 冲突不隐藏：两处来源不一致 → 都记并标 `source_conflict`。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照排除哪类混杂；参数在设备量程内；引用与规格真实无编造。
- [ ] 完整性：六要素 6 项均非空；sources/uncertainty/pending_human/limitations 完整。
- [ ] 可核验性：result.json 有版本/来源定位/金标准与 change_log；plan.md 与 result.json 一致。
- [ ] 使用增益：候选方案比较与参数裁剪有据（量程/预算/排期算术），避免模板式空话。
- [ ] 创新增益：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径至少落实一类。
- [ ] 易用性：按第 2/3/4 节可完成一次生成并判断可用。
- [ ] 稳定性：空输入→insufficient_evidence 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均识别并写 pending_human/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

## 7. 降级与不确定性策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列入 `pending_human` 与 `limitations`。
- 缺软件（pdftotext 不可用）：`degradations` 记录"无法提取 PDF 正文"，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突（时间窗 vs 样本量、参数超设备量程、预算不足）：`uncertainty` 写冲突双方与影响，给出两种可行替代，标需专家确认。
- 未确认项（安全/伦理/设备可用性）：写入 `pending_human`，绝不写成可执行事实。
- 写盘失败：降级为最小合法 result.json（case_id + status + errors），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`；绝不编数据/结论/评测分数。

## 8. 脚本与资源

- `scripts/main.py`：薄入口（probe / scaffold / validate 子命令），实现见 core_impl.py。
- `scripts/core_impl.py`：输入发现、骨架落盘、契约回填、算术校验、证据闸门、不确定性归并。
- `scripts/selftest.py`：包结构与三类异常输入自检，退出码 0=通过。
- `references/plan_schema_and_evidence.md`：字段语义、证据与 basis 规范、fallback 结构与可复用模板。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复。

## 9. 最小调用示例（示意，非正式案例结果）

输入（示意）：
```json
{"case_id":"demo-001","goal":"评估某抑制剂对某细胞系增殖的影响",
 "output_contract":{"plan":{},"evidence_and_risks":{},"confirmations":{}}}
```

调用：
```bash
cd /skills/weekly-skill
python3 scripts/main.py probe --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# ...分析并增量填写 result.json / plan.md ...
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
```

示意输出 result.json（骨架，非正式案例结果）：
```json
{"case_id":"demo-001","plan":{},"evidence_and_risks":{},"confirmations":{},
 "status":"partial","sources":[],"uncertainty":[],"pending_human":[],
 "limitations":[],"change_log":[{"seq":1,"event":"skeleton_written","detail":"partial skeleton"}]}
```
