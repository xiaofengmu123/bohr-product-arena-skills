---
name: litqa-trace-mini
description: 文献研究质量评价与可追溯科研检索（精简版）：围绕给定研究问题检索筛选文献，输出可复核的“维度—判断—证据—等级”结构化评价表；每条判断绑定逐字证据锚点（locator+quote），证据不足即弃权，风险等级由显式阈值聚合。当需要评价文献研究设计与报告质量、定位证据片段、判定偏倚风险并给出可复核分级，或输出可追溯候选文献与筛选依据时使用。
version: 1.1.0
---

# 文献质量评价与可追溯检索（litqa-trace-mini）

## 1. 角色与何时使用
本 Skill 把“检索筛选文献 + 逐条评价研究质量”沉淀为可复核流程。核心产出是**维度—判断—证据—等级**表，不是摘要改写：每条判断必须绑定原文可定位片段（`locator` + 逐字 `quote`），证据不足判 `insufficient_evidence` 并弃权，绝不“看起来合理”地补缺。
适用：给定科研问题，产出可追溯候选文献集、筛选/排序依据、遗漏说明，并对每篇重点文献给出结构化质量评价。
不适用：一次性查证、纯事实核验；不替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）
- 运行时输入目录 `/input/`：必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，需先扫描，不假设键名。
- `case.json` 含 `case_id` 与本题 `output_contract`（可能任意嵌套、键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约
必产出 `/app/output/result.json`，含：
```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "assessment": [ {"dimension","checkpoint","critical","verdict","judgment","locator","quote","basis"} ],
  "candidates": [], "screening": [], "evidence_records": [], "omissions": [],
  "sources": [ {"id","type","path_or_uri","locator","quote","note"} ],
  "text_index": { "源ID": "text/S1.txt" },
  "grade": { "level","risk_points","critical_fails","uncertain_count","rule" },
  "uncertainty": [], "pending_human": [], "change_log": [ {"time_utc","event","detail"} ],
  "errors": [], "degradations": [], "limitations": [],
  "runtime": { "start_epoch","skeleton_epoch","finish_epoch" }
}
```
- 主产物另给可读文本件 `/app/output/assessment.md`（评价表 + 证据锚点）。题目要求 Office/图件时，同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`。
- 不虚构引用/DOI/数值；缺失写 `pending_human` 或 `not_reported`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）
**铁律：前 3 分钟先落盘 partial 骨架，之后只做增量改进；每阶段结束增量写盘。**

| 阶段 | 时间 | 请求 | 动作（输入→输出） |
|-|-|-|-|
| P0 落盘骨架 | 0–3 min | ≤6 | 先执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`，立即产出合法 partial 骨架（含 case_id + 契约字段 + 空数组）。**必须最先完成。** |
| P1 发现与抽取 | 3–12 min | ≤28 | 扫描材料；用 `pdftotext`（不可用则记 `degradations`）抽文本到 `/app/work/text/<源ID>.txt`；用 `scripts/litqa.py locators` 生成段落/句子索引；识别研究问题与文献类型。写 `candidates`/`screening`/`evidence_records`/`sources`/`text_index`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 逐文献、逐维度、逐条目判定；每条带 `locator`+`quote`；用 `scripts/litqa.py verify` 核验引文逐字一致。填 `assessment`。 |
| P3 分级与冲突 | 21–25 min | ≤6 | 用 `scripts/litqa.py grade --inplace` 聚合总体风险等级；汇总冲突、遗漏、不确定性、limitations。写 `assessment.md`。 |
| P4 校验收尾 | 25–30 min | ≤2 | 执行 `scripts/main.py validate`；修正 error 级问题；设最终 `status`、补 `change_log`、`runtime.finish_epoch`。 |

原则：任何中途异常都不允许留下空产物，`status` 保持可解释的 partial/insufficient_evidence。

## 5. 自检清单（交付前逐条勾选）
完整清单见 `references/checklist.md`。核心项：
- [ ] 产出“维度—判断—证据—等级”表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）+ 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 通过，fail=0）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `scripts/main.py validate` 无 error 级问题；`scripts/main.py self-test` 通过（空目录/坏 JSON/缺输入三种均退出码 0）。
- [ ] 全文不可得/仅摘要/图表不可解析/预印本或语言特殊/关键证据缺失等已如实标注，未编造。

## 6. 降级策略
- 缺数据：不足以支撑某维度时该条判 `insufficient_evidence`，`status` 相应降为 `insufficient_evidence` 或 `partial`，`limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库；`pdftotext` 不可用时保留原始 PDF 并在 `degradations` 记“未抽取文本”，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成。
- 重复运行：确定性流程（无随机数、固定定位与阈值），同一输入结果一致。

## 7. 脚本（安装后位于 `/skills/weekly-skill/`，均在 Skill 根目录下以相对路径运行）
- `scripts/main.py`：`skeleton` 立即落盘 / `inspect` 输入盘点 / `validate` 结构校验 / `self-test` 三场景自检。
- `scripts/litqa.py`：`locators` 段落·句子索引 / `verify` 逐字引文核验 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：自检入口（等价 `main.py self-test`）。

## 8. 量表与判据
维度定义、文献类型特有检查点、判定值域、分级阈值、弃权规则、证据定位语法，全部见 `references/rubric.md`；这是评价唯一事实标准，逐条对照执行，不得自由发挥。
