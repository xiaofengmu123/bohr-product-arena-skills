---
name: litqa-evidence
description: 文献研究质量评价与可追溯科研检索：围绕研究问题检索筛选文献，产出"维度—判断—证据—等级"结构化评价表，每条判断绑定可定位的逐字证据（locator+quote），证据不足即弃权不补造；result.json 逐字段对齐题目 output_contract，并以结构化 change_log 记录全过程变更。适用于需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险并给出可复核分级，或需要输出可追溯候选集、筛选依据与遗漏说明的场景。
version: 1.0.0
---

# 文献研究质量评价与可追溯检索（litqa-evidence）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核流程。核心产出是**维度—判断—证据—等级**表，而非摘要改写：每条判断对应原文可定位片段（`locator` + 逐字 `quote`），证据不足时明确弃权，不允许"看起来合理"地补缺。

适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。不适用：一次性查证、纯事实核验、替代同行评审或临床/政策决策。

本版两项设计取舍（对应"输出契约完整"变体）：
1. **契约优先**：启动即递归定位 `output_contract`，先落盘一份"字段齐备"骨架；每个契约字段的发现路径、类型解析、默认值与保留的未知键写入 `contract_alignment`，保证 `result.json` 与题目契约逐字段对齐、不改名、不删键。
2. **全程 change_log 审计**：每次写盘（骨架→增量→终稿）追加一条结构化事件（`seq/time_utc/event/stage/reason/detail`），`status` 变更也记录，人工可逐条回放。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，其中必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意命名/嵌套层级，键名大小写不敏感）。**先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 优先读取并容错（BOM、坏编码、坏 JSON 降级，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文，不依据错误的领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，顶层顺序：`case_id`、`status`，随后**逐字段回填 output_contract 的全部字段**，再追加本 Skill 的结构字段：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "contract_alignment": { "status", "contract_path", "shape", "field_count", "fields", "unknown_keys_preserved", "required_fields" },
  "assessment": [ {"id","dimension","checkpoint","critical","verdict","judgment","locator","quote","basis","confidence"} ],
  "candidates": [], "screening": [], "evidence_records": [], "omissions": [],
  "grade": { "risk_level", "rationale", "counts", "thresholds" },
  "sources": [ {"id","type","path_or_uri","locator","quote","note"} ],
  "uncertainty": [], "pending_human": [],
  "errors": [], "degradations": [], "limitations": [],
  "change_log": [ {"seq","time_utc","event","stage","reason","detail"} ],
  "runtime": { "start_epoch", "skeleton_epoch", "finish_epoch" }
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- 主产物附可读文本 `/app/output/assessment.md`（评价表 + 证据锚点）。题目要求 Office/图件时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`。缺失写 `pending_human` 或 `not_reported`，不虚构引用、DOI、数值。

## 4. 执行流程（分步骤 + 时间/请求预算）

总预算 **900 秒 / 64 次模型请求**，单案例单次运行。**前 3 分钟必须先落盘骨架**，之后只做增量改进；每阶段结束都增量写盘，任何中途异常不得留下空产物。

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | `python3 -B scripts/main.py skeleton`（缺省 `/input`→`/app/output`、`/app/work`）。输出字段齐备的 `result.json` 骨架（case_id + 契约字段 + `contract_alignment` + 空数组 + 首条 change_log）。**最先完成**。 |
| P1 发现与读取 | 3–12 min | ≤28 | 扫描材料；`python3 -B scripts/litqa.py locators` 生成稳定段落定位；明确研究问题、检索范围、文献类型与筛选依据。输出 `/app/work/` 文本/定位中间件 + `evidence_records`/`candidates`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 逐文献、逐维度、逐条目判定，每条带 `locator`+`quote`；`python3 -B scripts/litqa.py verify` 抽查逐字证据。输出填充 `assessment`。 |
| P3 分级与冲突 | 21–25 min | ≤6 | `python3 -B scripts/litqa.py grade` 按阈值聚合风险等级；汇总冲突、遗漏、不确定性、limitations。输出 `assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | `python3 -B scripts/main.py validate`（无 error 级问题）；设最终 `status`，写 `runtime.finish_epoch` 与终稿 change_log。输出最终 `result.json`。 |

## 5. 自检清单（逐条可勾选，对应评分维度）

完整清单见 `references/checklist.md`；交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）+ 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 抽查通过）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段（`contract_alignment.field_count` 与契约一致）、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/selftest.py` 通过（空目录/坏 JSON/缺输入/确定性/证据核验，退出码 0）。
- [ ] 全文不可得/仅摘要/图表不可解析/预印本或语言特殊/关键证据缺失均已如实标注，未编造。

## 6. 降级策略

- 缺数据：不足以支撑某维度时判 `证据不足`，`status` 降为 `insufficient_evidence`/`partial`，`limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库；`pdftotext` 不可用时保留原始 PDF，`degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成/未完成部分。
- 重复运行：确定性流程（无随机数、固定定位与阈值、固定字段顺序），同一输入的分析结果一致；仅时间戳为运行元数据。

## 7. 脚本（均在 SKILL 根目录以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 校验结构与契约对齐。
- `scripts/litqa.py`：`locators` 段落定位 / `verify` 逐字引文核验 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：五场景自检（空目录/坏 JSON/缺输入/契约回填/确定性/证据核验）。

全部只依赖 Python 标准库；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`（可用环境变量 `SKILL_INPUT_DIR/SKILL_OUTPUT_DIR/SKILL_WORK_DIR` 覆盖）。
