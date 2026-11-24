---
name: litqa-veritas
description: 文献研究质量评价与可追溯科研检索：围绕给定研究问题检索、筛选并排序文献，对每篇重点文献产出“维度—判断—证据—等级”结构化评价表，每条判断带可定位的逐字证据与证据充分性等级，并把不确定性、限制与降级情况显式化、可复核。当需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险、给出可复核分级，或需要输出可追溯候选集、筛选依据与遗漏说明时使用。
version: 1.1.0
---

# 文献研究质量评价与可追溯检索（litqa-veritas）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核流程。适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。核心产出是 **维度—判断—证据—等级** 表，而非摘要改写：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），并标注 **证据充分性等级**；证据不足时明确弃权，不允许"看起来合理"地补缺。

本版本（v1.1.0）相较通用做法，把 **不确定性（uncertainty）与限制（limitations）显式化为结构化字段**，并规定降级规则：材料缺失、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等缺口，必须落到 `limitations`/`uncertainty`/`degradations`，并据此下调 `status` 与分级，不得编造。

不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录：`/input/`，其中必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，需先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都要降级，不崩溃）；单文件 >2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，结构如下（`<…>` 为题目 `output_contract` 字段逐字回填）：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "research_question": "…",
  "retrieval": {
    "scope": {"design_types": [], "inclusion": [], "exclusion": []},
    "queries": [{"query","source","time","n_results","note"}],
    "candidates": [{"id","title","type","source_uri","relevance","relevance_basis"}],
    "screening": [{"candidate_id","criterion","decision","basis"}],
    "ranked": [{"id","rank","rationale"}],
    "omissions": [{"item","reason","status"}]
  },
  "assessment": [
    {"id","source_id","dimension","checkpoint","critical","verdict",
     "evidence_level","judgment","locator","quote","basis","confidence","uncertainty_refs"}
  ],
  "grades": {"per_dimension": {}, "per_source": {}, "overall": "…"},
  "sources": [{"id","type","path_or_uri","locator","quote","note"}],
  "uncertainty": [{"id","kind","level","scope","statement","evidence_gap","mitigation","status"}],
  "limitations": [{"id","scope","description","impact","status"}],
  "errors": [], "degradations": [], "pending_human": [],
  "change_log": [{"time_utc","event","detail"}],
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，部分可得写 `partial`，不编造数据或结论。
- 主产物还要有可读文本交付件：`/app/output/assessment.md`（评价表 + 证据锚点 + 不确定性/限制清单）。若题目要求 Office/图件，同时给出真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多个来源不一致时全部记录并在 `uncertainty` 标 `kind=conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 4. 核心方法：证据充分性阶梯、不确定性模型与分级

### 4.1 证据充分性阶梯（每条判断必标 `evidence_level`）

| `evidence_level` | 含义 | 允许产出 |
|---|---|---|
| `direct` | 引文逐字存在于所标 `locator` | 判断 + quote |
| `paraphrase` | 判断由所标位置支持，但非逐字（记录依据句而非"引文"） | 判断 + basis |
| `inference` | 由所标位置按已写明的推理规则推出 | 判断 + basis + 规则 |
| `absent` | 原文未报告 | 仅 `verdict=insufficient_evidence`，不写实质判断 |

弃权规则：任何一条 `critical=true` 的检查点证据为 `absent` 时，该维度不得给正向结论；该文献整体等级上限降为 `insufficient_evidence` 或 `unclear`。

### 4.2 不确定性模型（`uncertainty` 数组，每条为结构化对象）

每条含 `kind`（`evidential | methodological | conflict | access | inference`）、`level`（`low | moderate | high | critical`）、`scope`（影响的检查点/维度/结论）、`statement`（不确定什么）、`evidence_gap`（缺口证据）、`mitigation`（已采取的缓解）、`status`（`open | resolved_by_degradation | pending_human`）。不确定性可向上传播：被 `critical` 不确定性影响的维度与结论必须相应降级。

### 4.3 分级阈值（`grade` 汇总）

- `low`：关键检查点全部有 `direct/paraphrase` 证据且无 `high/critical` 偏倚信号。
- `moderate`：存在 `moderate` 偏倚或个别 `inference` 证据，但关键结论仍被直接支持。
- `high`：存在 `high` 偏倚信号或多个关键检查点仅 `inference`。
- `unclear`：关键证据缺失但非关键部分可得，或来源冲突未裁决。
- `insufficient_evidence`：关键检查点证据 `absent`，或全文不可得。

阈值与量表明细见 `references/rubric.md`；汇总必须写明"等级由哪些维度结果如何汇总而来"，不孤立给标签。

## 5. 执行流程（分步骤 + 时间预算）

总预算：**900 秒 / 64 次模型请求**（单案例单次运行）。**前 3 分钟必须先落盘骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | 执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出：`/app/output/result.json`（partial 骨架，含 case_id + 契约字段 + 空数组）。**此步必须最先完成**。 |
| P1 发现与读取 | 3–12 min | ≤28 | 扫描材料；执行 `python3 -B scripts/litqa.py locators --input /input --work /app/work` 生成稳定段落定位；识别研究问题与文献类型；记录检索范围、查询、候选与筛选依据。输出：`/app/work/locators.json` + `retrieval`/`candidates`/`screening`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 量表，逐文献、逐维度、逐条目判定；每条带 `locator`+`quote`+`evidence_level`；执行 `python3 -B scripts/litqa.py verify --work /app/work --result /app/output/result.json` 核验逐字引文。输出：填充 `assessment`。 |
| P3 分级与不确定性 | 21–25 min | ≤6 | 执行 `python3 -B scripts/litqa.py grade --result /app/output/result.json` 聚合风险等级；显式汇总冲突、不确定性、limitations、degradations。输出：`assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | 执行 `python3 -B scripts/main.py validate --input /input --output /app/output`；修正 error 级问题；设 `status` 为最终值，写 `change_log`、`runtime.finish_epoch`。输出：最终 `result.json`。 |

原则：每个阶段结束都增量写盘（骨架 → 部分评价 → 完整评价）；任何中途异常都不允许留下空产物，`status` 保持可解释的 `partial`/`insufficient_evidence`。

## 6. 降级与不确定性策略（本版侧重）

- **缺数据**：材料不足以支撑某维度时，该条目 `verdict=insufficient_evidence`、`evidence_level=absent`，并在 `limitations` 写清缺口、在 `uncertainty` 标 `evidence_gap`；`status` 相应降为 `insufficient_evidence` 或 `partial`，不补造。
- **缺软件**：只依赖 Python 标准库；若 `pdftotext` 等工具不可用，保留原始 PDF 并在 `degradations` 记录"未抽取文本"，仅评可得部分。
- **冲突**：多来源不一致时全部记录，`uncertainty` 标 `kind=conflict`，该指标分级至少 `unclear`。
- **权限/接口异常**：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- **重复运行**：确定性流程（无随机数、固定定位与阈值），同一输入结果一致。

## 7. 自检清单（交付前逐条勾选，完整清单见 references/checklist.md）

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）覆盖 + 文献类型特有检查点。
- [ ] 每条判断标 `evidence_level`；`direct` 的 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 抽查通过）。
- [ ] 不确定性/限制显式化：`uncertainty` 每条含 `kind/level/scope/statement/evidence_gap`；`limitations`/`degradations` 如实记录。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/main.py self-test` 通过（空目录/坏 JSON/缺输入三种均退出码 0）。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等已如实标注，未编造。

## 8. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构 / `self-test` 三场景自检。用法见 §5 与 `--help`。
- `scripts/litqa.py`：`locators` 段落定位 / `verify` 逐字引文核验 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：独立自检入口，等价于 `scripts/main.py self-test`（覆盖空目录/坏 JSON/缺输入/契约回填四场景，全部通过退出码 0）。
