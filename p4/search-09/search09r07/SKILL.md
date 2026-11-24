---
name: litsearch-audit
description: 可追溯科研文献检索与质量评价：围绕研究问题检索、筛选文献，保留候选集、排序/筛选依据与遗漏说明，并对重点文献产出"维度—判断—证据—等级"结构化评价表，每条判断带可定位的逐字证据；证据不足时弃权而非补造。当需要检索筛选文献、定位证据片段、判定偏倚风险、给出可复核分级并输出可追溯候选集与筛选依据时使用。
version: 1.0.0
---

# 可追溯科研文献检索与质量评价（litsearch-audit）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核流程，解决泛化摘要式评价的两个缺陷：只给等级不给证据位置、证据不足却补一个"看起来合理"的判断。核心产出是 **维度—判断—证据—等级** 表（`assessment`）加上可追溯的检索/筛选记录（`candidates`/`screening`/`omissions`），每条判断对应原文可定位片段（`locator` + 逐字 `quote`）。

适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给结构化质量评价与分级。不适用：一次性查证、纯事实核验；不替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，需先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意命名/嵌套，键名大小写不敏感）。**必须先解析并原样回填其全部字段**：缺值给 `null`/`[]`，不改名、不删未知键。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 都要降级，不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，结构如下：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "research_question": null,
  "search": {"scope","query","date_range","language","sources_queried":[]},
  "candidates": [{"id","title","authors","year","venue","doi","source_uri","relevance","screening_decision","rationale","locator","quote"}],
  "screening": [{"stage","criterion","decision","reason"}],
  "assessment": [{"dimension","checkpoint","critical","verdict","judgment","locator","quote","basis"}],
  "evidence_records": [], "omissions": [],
  "sources": [{"id","type","path_or_uri","locator","quote","note"}],
  "uncertainty": [], "change_log": [{"time_utc","event","detail"}],
  "errors": [], "degradations": [], "pending_human": [], "limitations": [],
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- 主产物还要有可读交付件 `/app/output/assessment.md`（评价表 + 证据锚点）。题目要求 Office/图件时，同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致时全部记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
- 判定值受限于 `references/rubric.md` 词表；分级由 `grade` 阈值聚合并可解释。

## 4. 执行流程（分步骤 + 时间预算）

总预算 **900 秒 / 64 次模型请求**（单案例单次运行）。前 3 分钟必须先落盘骨架，之后只做增量改进。

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | 输入 `/input`。执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出 `/app/output/result.json`（partial 骨架，含 case_id + 契约字段 + 空数组）。**此步必须最先完成。** |
| P1 发现与读取 | 3–12 min | ≤28 | 扫描材料；用 `python3 -B scripts/main.py locators --text-file <txt> --source-id <id> --out <json>` 生成稳定段落/句子定位；识别研究问题与文献类型；记录检索范围与筛选依据。输出 `/app/work/` 中间件 + `evidence_records`/`candidates`/`screening`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 量表，逐文献、逐维度、逐条目判定；每条带 `locator`+`quote`；核验用 `python3 -B scripts/main.py verify`。输出：填充 `assessment`。 |
| P3 分级与冲突 | 21–25 min | ≤6 | 用 `python3 -B scripts/main.py grade` 聚合风险等级；汇总冲突、遗漏、不确定性、limitations。输出 `assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | 执行 `python3 -B scripts/main.py validate --input /input --output /app/output`；修正 error 级问题；设 `status` 为最终值，写 `change_log`、`runtime.finish_epoch`。输出最终 `result.json`。 |

原则：每阶段结束都增量写盘（骨架 → 部分评价 → 完整评价）；任何中途异常都不允许留下空产物，`status` 保持可解释的 partial/insufficient_evidence。

## 5. 自检清单

完整清单见 `references/checklist.md`，交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；四类维度（研究设计/证据充分性/偏倚风险/报告完整性）+ 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`verify` 抽查通过）。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`。
- [ ] `python3 -B scripts/main.py validate` 无 error 级问题；`python3 -B scripts/selftest.py` 退出码 0（空目录/坏 JSON/缺输入/超大文件/坏编码均不崩溃）。

## 6. 降级策略

- 缺数据：不足以支撑某维度时，该条目判 `证据不足`（`insufficient_evidence`），`status` 相应降为 `insufficient_evidence` 或 `partial`，在 `limitations` 写清缺口，不补造。
- 缺软件：只依赖 Python 标准库；若 `pdftotext` 等工具不可用，保留原始 PDF 并在 `degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- 鲁棒性：空输入目录、坏 JSON、坏编码、缺参数、超大文件、写盘失败均降级为最小合法产物并记录，不抛异常；同一输入重复运行结果一致（无随机数、固定定位与阈值）。

## 7. 脚本（均在 SKILL 根目录下以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `locators` 段落句子定位 / `verify` 逐字核验 / `grade` 阈值分级 / `validate` 校验结构 / `self-test` 鲁棒性自检。用法见 §4 与 `--help`。
- `scripts/core_impl.py`：核心实现（I/O 容错、契约定位、定位/核验、分级、校验、自检）。
- `scripts/selftest.py`：独立自检（鲁棒性场景 + 端到端最小调用示例，示例内容标注"示例"）。

## 8. 最小调用示例（示意，非正式案例）

```bash
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py locators --text-file /app/work/paper.txt --source-id paper --out /app/work/paper_loc.json
python3 -B scripts/main.py verify --locators /app/work/paper_loc.json --locator paper#p1 --quote "randomized 120 patients"
python3 -B scripts/main.py grade --assessments /app/work/assessment.json
python3 -B scripts/main.py validate --input /input --output /app/output
python3 -B scripts/selftest.py
```

示意输出结构见 `scripts/selftest.py` 内 `demo_end_to_end()`，全部标注为示例，不冒充正式案例结果。

## 9. 边界与价值

相对"通用摘要改写"：本 Skill 用量表限定维度与判定值，每条判断锚定原文片段，缺证据可弃权；相对公开报告清单（CONSORT/PRISMA 等）与编辑初筛：本 Skill 聚焦"可复核的质量分级 + 可追溯检索筛选"，不覆盖清单的全部条目，也不替代人工裁决，而是把人工改动沉淀为量表/阈值改进的入口。
