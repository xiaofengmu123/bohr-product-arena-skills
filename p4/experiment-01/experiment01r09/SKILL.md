---
name: scix-plan-lite
description: 为科研案例生成、比较、优化并复核可排期可执行的实验方案（假设与变量、对照组、设备材料、时间成本、参数范围、风险异常），对多候选方案做预算/排期/量程/参数域算术校验，并给出逐字证据溯源与风险/确认项。当需要把研究目标转成可执行实验方案、比较修订候选方案或校验参数与预算时使用。
version: 1.1.0
---

# scix-plan-lite：实验方案生成、比较与复核

## 1. 角色与何时使用

本 Skill 把「研究目标 + 材料 + 约束 + 候选方案」转成**可排期、可执行、可复核**的实验方案：目标落成可检验假设，概念落成操作变量，变量配对照与判读标准，步骤绑定设备/试剂/参数/时长，并预设失败与异常处置。特别适配组织方提供的 3 个版本化候选方案、前瞻性资源/价格/预约合同、有限参数域与真实算术校验场景。

不适用：纯文献综述、数据分析建模、论文写作；不替代安全/伦理审批。

硬性原则（贯穿全程）：只依据 `/input` 真实材料下结论，不编数值/型号/价格/批准/评分；关键结论带 `locator`+逐字 `quote`；冲突两处都记并标 `source_conflict`；不确定写 `uncertainty`，缺失写 `not_reported`，待确认列 `pending_human`。

## 2. 输入契约（运行时）

- 输入目录 `/input/`：至少含 `case.json`（可能带 `output_contract`）与 `task.md`；可能有 `materials/`、PDF 原文、资源/价格/预约合同、候选方案文件。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 `result.json`）。
- 先扫描再判断，不假设固定文件名/键名；文本用 `utf-8-sig→utf-8→gb18030→latin-1` 容错读，单文件 >2MB 跳过并记 `degradations`；PDF 用 `pdftotext` 提取。
- 脚本调用（相对 Skill 根目录；先 `cd /skills/weekly-skill` 或用绝对前缀；本地冒烟可用 `--input/--output/--work` 覆盖）：

```bash
python3 scripts/main.py probe    --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 scripts/main.py render   --output /app/output
python3 scripts/main.py audit    --output /app/output --write
python3 scripts/main.py check    --input /input --output /app/output --work /app/work
```

`all` 子命令 = probe→scaffold→render→audit--write→check。

## 3. 输出契约（必产出 /app/output/result.json）

1. `case_id`：取 case.json 的 `case_id/id/challenge_id`，缺失取 task.md 的 `challenge_id`，仍无写 `"unknown"` 并列 `pending_human`。
2. 题目 `output_contract` 的**全部字段**：有契约就逐键镜像塑形（缺值 `null`/`[]`，不改名、不删未知键）；无契约用 fallback 结构（见 `references/contract_and_checklist.md`）。
3. 信封：`status`（`ok|partial|insufficient_evidence|input_error`）、`sources`、`uncertainty`、`pending_human`、`limitations`、`degradations`、`errors`、`change_log`、`runtime`、`artifacts`。
4. 可编辑主产物 `/app/output/plan.md`：由 `render` 从 result.json 生成，两者一致；本任务以 `.md/.json` 为可编辑格式，不强制 Office。

字段语义与元素字段（变量/对照/参数/预约等）见 `references/contract_and_checklist.md`。参数必须带 `basis` 枚举（`instrument_manual|reagent_label|organizer_contract|literature|pilot_data|kit_insert|assumption`）；非 `assumption` 的数值必须给 `basis_locator`。案例内置的「前瞻性合同」只标「案例给定约束（非真实报价/审批）」。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 tokens）

| 步 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | `/input` → `probe` 得 `input_probe.json`；定 case_id、output_contract、材料/PDF/合同/候选方案清单 |
| 2 骨架落盘 | ≤120s | ≤3 | `scaffold` 写合法 partial result.json + plan.md 骨架。**前 3 分钟内必须完成**；此后任何时刻 result.json 不得缺失/损坏 |
| 3 证据提取 | ≤240s | ≤12 | 读 PDF/合同/候选方案，抽数值与逐字片段，冲突与缺口记入 work 笔记 |
| 4 方案生成与修订 | ≤240s | ≤18 | 目标→假设→变量→对照→参数→资源→判读；对 3 候选方案做参数/量程/预算/排期算术校验并记录修订理由；写 result.json 与 plan.md |
| 5 自检修复 | ≤180s | ≤18 | `check` 按报告修结构/类型/算术/证据；增量更新 change_log |
| 6 收尾 | ≤60s | ≤10 | 重跑 `check`，确认 status 诚实、sources/uncertainty/pending_human 完整，写 runtime |

硬规则：先落盘再分析；每次大改动原子写回（写 `.tmp` 再 `os.replace`）；change_log 记 `{ts,event,detail}`；重复运行内容字段一致（仅 ts 取当前时间）。

## 5. 证据溯源与诚实性

- 关键结论/数值可回溯：`locator`（如 `materials/paper.md` 或 `paper.pdf#p3`）+ 逐字 `quote`；`check` 会在本地文本中逐字核对 quote 是否真实存在。
- 冲突不隐藏：两处来源不一致 → 都记并标 `source_conflict`。
- 不虚构引用/DOI/数值/价格/排期；缺失写 `not_reported` 或 `pending_human`。
- 参数依据标 `basis`；有限参数域须落在给定 `domain` 内；预算、预约机时、样本量、关键链时长须通过 `audit` 的真实算术。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素全部可操作（变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长）。
- [ ] 正确性：假设可检验；变量与测量方式匹配；每个对照写明排除哪类混杂；参数在设备量程与给定域内；引用与规格真实无编造。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险异常 6 项均非空。
- [ ] 可核验性：result.json 有版本/来源定位/金标准与 change_log；plan.md 与 result.json 一致（`check` 验证）。
- [ ] 使用增益：3 个候选方案比较与参数裁剪有据（量程/预算/排期算术），拒绝者写 `rejected_reason`，选中者通过全部硬约束。
- [ ] 创新增益：至少落实一类——按量程与预算裁剪参数、按研究目标反推对照、生成风险预案与替代路径、按契约约束修订候选方案。
- [ ] 易用性：按第 2/4 节可完成一次生成并判断可用。
- [ ] 稳定性：空输入→`insufficient_evidence` 不崩溃；坏 JSON/编码→记录后继续；重复运行一致。
- [ ] 异常处理：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定均被识别并写 `pending_human`/替代路径。
- [ ] 生态贡献：输入清单/输出结构/依赖/示例/限制齐全，模板与清单可复用（见 references/）。

交付前运行：`python3 scripts/selftest.py` 应全绿；`python3 scripts/main.py all ...` 应能跑通并让 `check` 退出码为 0（允许警告）。

## 7. 降级策略（不允许编造）

- 缺材料/数值：字段写 `not_reported`，status 降为 `partial`/`insufficient_evidence`，缺口列 `pending_human` 与 `limitations`。
- 缺软件（pdftotext 不可用）：`degradations` 记「无法提取 PDF 正文」，改用材料文本字段；仍无法判断则如实标注。
- 约束冲突：`uncertainty` 写冲突双方与影响，给两种可行替代，标需专家确认。
- 写盘失败：降级为最小合法 result.json（`case_id`+`status`+`errors`），不崩溃。
- 任何情况 status 诚实：材料不足即 `insufficient_evidence`，绝不编数据/结论/评测分数。

## 8. 最小调用示例（示意，非正式案例结果）

输入（示意）：
```json
{"case_id":"demo-001","goal":"评估某抑制剂对某细胞系增殖的影响",
 "output_contract":{"plan":{"variables":[],"parameters":[]},"evidence":{"evidence":[]},"confirmations":{"safety":[]}}}
```

```bash
cd /skills/weekly-skill
python3 scripts/main.py all --input /input --output /app/output --work /app/work
# 分析并增量填写 result.json → render → audit --write → check 直到退出码 0
```

示意输出（骨架，非正式案例结果）：
```json
{"case_id":"demo-001","plan":{"variables":[],"parameters":[]},"evidence":{"evidence":[]},"confirmations":{"safety":[]},
 "status":"partial","sources":[],"uncertainty":[],"pending_human":[],"limitations":[],"change_log":[{"ts":"...","event":"skeleton_written"}]}
```

## 9. 脚本与资源

- `scripts/main.py`：入口包装（<30KB，实现见 `scripts/core_impl.py`）。
- `scripts/core_impl.py`：probe/scaffold/render/audit/check 的确定性实现（仅标准库）。
- `scripts/selftest.py`：合成夹具自检（空输入/坏 JSON/契约镜像/算术/逐字引用/渲染确定性）。
- `references/contract_and_checklist.md`：字段语义、元素字段、证据规范、自检评分映射与失败修复。
