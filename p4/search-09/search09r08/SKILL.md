---
name: litqa-scoremap
description: 文献研究质量评价与可追溯科研检索：把题目评分维度逐条映射到执行流程与自检项，围绕给定研究问题检索筛选文献，并对每篇重点文献产出"维度—判断—证据—等级"结构化评价表，每条判断带可定位的逐字证据（locator+quote）；候选集、排序/筛选依据、遗漏与冲突均保留来源定位，缺证据时明确弃权而非补造。当需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险并给出可复核分级，或需要输出可追溯候选文献与筛选依据时使用。
version: 2.0.0
---

# 文献研究质量评价与可追溯科研检索（litqa-scoremap）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核、可对照评分维度自检的流程。适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。核心产出是**维度—判断—证据—等级**表，而非摘要改写：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），证据不足时明确弃权（`verdict="证据不足"`），不允许"看起来合理"地补缺。

本版核心变体：**评分维度对齐**——把题目 5 个评分维度（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）逐条映射到流程阶段、`result.json` 字段与自检项（见 `references/rubric.md` §1），使每个产出都能回答"它为哪一个评分点提供证据"。

不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，其中必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都降级，不崩溃）；单文件 >2MB 跳过并记录。
- 中间产物写 `/app/work/`，最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据；不虚构引用、DOI、数值或检索命中数。

## 3. 输出契约

必产出 `/app/output/result.json`，结构（含题目 `output_contract` 全部字段原样回填到顶层）：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "artifacts": [], "research_question": null, "search_scope": null,
  "queries": [], "candidates": [], "screening": [], "assessment": [],
  "grade": null, "evidence_records": [], "omissions": [], "conflicts": [],
  "sources": [ {"id","type","path_or_uri","locator","quote","note"} ],
  "uncertainty": [], "pending_human": [], "rubric_alignment": [],
  "change_log": [ {"time_utc","event","detail"} ],
  "errors": [], "degradations": [], "limitations": [],
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `assessment` 每条含 `dimension`、`checkpoint`、`critical`、`verdict`、`judgment`、`locator`、`quote`、`basis`、`evidence_level`。
- `grade` 给出整体风险等级、置信度、汇总理由与所用阈值；等级必须由维度结果汇总而来，不孤立给标签。
- 主产物还要有可读文本交付件 `/app/output/assessment.md`（评价表 + 证据锚点 + 复核分歧记录模板）。若题目要求 Office/图件，同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`。

## 4. 执行流程与预算（900 秒 / 64 次模型请求）

总预算 **900 秒 / 64 次模型请求**（单案例单次运行）。**前 3 分钟必须先落盘骨架**，之后只做增量改进；任何中途异常都不允许留下空产物。

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–180s | ≤6 | 输入 `/input`。执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出 `/app/output/result.json`（partial 骨架，含 case_id + 契约字段 + 空数组）。**此步必须最先完成**。 |
| P1 发现与读取 | 180–540s | ≤30 | 扫描材料；用 `scripts/litqa.py extract`（或环境 pdftotext）抽取文本，`scripts/litqa.py locators` 生成稳定段落定位；识别研究问题与文献类型；解析 Crossref/EuropePMC 元数据响应，记录检索式、命中数、纳入排除标准。输出 `/app/work/` 中间件 + `queries`/`candidates`/`screening`/`evidence_records`。 |
| P2 逐维度评价 | 540–780s | ≤22 | 按 `references/rubric.md` 量表，逐文献、逐维度、逐条目判定；每条带 `locator`+`quote`；核验用 `scripts/litqa.py verify`。输出填充 `assessment`。 |
| P3 分级与冲突 | 780–860s | ≤5 | 用 `scripts/litqa.py grade` 做确定性聚合交叉检查；汇总冲突、遗漏、不确定性、limitations；写 `grade`、`rubric_alignment`。输出 `assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 860–900s | ≤1 | 执行 `python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work`；修正 ERROR 级问题；设 `status` 最终值，补 `change_log`、`runtime.finish_epoch`。输出最终 `result.json`。 |

原则：每个阶段结束都增量写盘（骨架 → 部分评价 → 完整评价），确定性流程（无随机数、固定定位与阈值），同一输入结果一致。

## 5. 自检清单

完整清单见 `references/checklist.md`，交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）+ 文献类型特有检查点。
- [ ] 每条 `critical` 判断的 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 无 MISSING）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`pending_human`/`change_log`/`runtime`。
- [ ] `scripts/main.py validate` 报告无 ERROR；`scripts/selftest.py` 通过（frontmatter/体积/四场景/--help）。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等已如实标注，未编造。

## 6. 降级策略

- 缺数据：材料不足以支撑某维度时，该条目判 `证据不足`，`status` 降为 `insufficient_evidence` 或 `partial`，在 `limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库；`pdftotext` 不可用时保留原始 PDF，在 `degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`/`conflicts`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- 坏 JSON/坏编码/缺输入/空目录：`scripts/main.py self-test` 四场景均退出码 0，产出合法结果不崩溃。

## 7. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构 / `self-test` 四场景自检。用法见 §4 与 `--help`。
- `scripts/litqa.py`：`locators` 段落定位 / `verify` 逐字引文核验 / `grade` 阈值聚合分级 / `extract` 调用 pdftotext。
- `scripts/selftest.py`：交付前总自检（frontmatter、体积约束、四场景、--help）。
