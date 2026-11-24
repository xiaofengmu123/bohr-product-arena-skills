---
name: litqa-evidence
description: 文献研究质量评价与证据锚定检索：围绕研究问题检索筛选文献，产出"维度—判断—证据—等级"评价表；每条判断绑定可定位的逐字证据（locator+quote+SHA256+char_span+验证状态）与细粒度 evidence_map，证据不足时明确弃权。当需要检索筛选科研文献、评价研究设计与报告质量、定位证据片段、判定偏倚风险、给出可复核分级，或输出可追溯候选集与筛选依据时使用。
version: 1.0.0
---

# 文献研究质量评价与证据锚定检索（litqa-evidence）

## 1. 角色与何时使用

本 Skill 把"检索筛选文献 + 评价文献研究质量"沉淀为可复核流程。核心产出是 **维度—判断—证据—等级** 表，
以及比单一 `evidence_records` 更细的 `evidence_map`：每条判断不止带 `locator + quote`，
还带 `quote_sha256`、`char_span`、证据来源层（fulltext/abstract/table_figure/metadata）、
逐字匹配验证状态（verbatim/fuzzy/none/unverifiable）。每条结论要么指向可逐字回溯的证据，
要么明确弃权（`verdict=insufficient_evidence`），不允许"看起来合理"地补缺。

适用：给定科研问题，产出可追溯候选集、排序/筛选依据、遗漏说明，并对每篇重点文献给出结构化质量评价。
不适用：一次性查证；纯事实核验；替代同行评审、临床/政策决策或研究结论。

## 2. 输入契约（先发现，再假设）

- 运行时输入目录 `/input/`，必有 `case.json` 与 `task.md`；材料（PDF/文本/元数据响应等）路径与文件名不固定，先扫描识别，不假设键名。
- `case.json` 内含 `case_id` 与本题 `output_contract`（可能任意嵌套，键名大小写不敏感）。必须解析并**原样回填其全部字段**到 `result.json` 顶层：缺值给 `null`/`[]`，不改名、不删未知键；与结构保留键冲突时双方都保留并记录。
- 文本按 `utf-8-sig` 优先读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记录。
- 工作目录 `/app/work/`（中间产物），最终产物与可编辑交付件写 `/app/output/`。
- 事实依据是实际输入文件与原论文；不依据错误领域标签补造数据。

## 3. 输出契约

必产出 `/app/output/result.json`，结构如下：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 字段逐字回填>": null,
  "assessment": [ {"id","dimension","checkpoint","critical","verdict","judgment","locator","quote","evidence_ids","basis"} ],
  "evidence_map": [ {"evidence_id","claim_id","locator","quote","quote_sha256","quote_normalized","char_span","provenance","match","verified","basis","note"} ],
  "candidates": [], "screening": [], "omissions": [], "sources": [],
  "uncertainty": [], "pending_human": [], "change_log": [],
  "errors": [], "degradations": [], "limitations": [],
  "runtime": {"start_epoch","skeleton_epoch","finish_epoch"}
}
```

- `case_id` 必填；`status` 诚实：材料不足写 `insufficient_evidence`，不编造数据或结论。
- `assessment` 每条判断都必须绑定 `locator + quote` 并回指 `evidence_map` 的 `evidence_ids`；不可定位时 `verdict=insufficient_evidence` 并弃权。
- `evidence_map` 为细粒度证据：每条含 `quote_sha256`、`char_span`、`provenance`、`match` 与 `verified`，可被 `scripts/litqa.py verify` 机器核验。
- 主产物另有可读文本交付件 `/app/output/assessment.md`（评价表 + 证据锚点）。若题目要求 Office/图件，同时给真实渲染 PDF/PNG 预览；不要求则省略。
- 冲突信息不隐藏：同一指标多来源不一致全部记录并标 `source_conflict`；不虚构引用、DOI、数值。

## 4. 执行流程（分步骤 + 时间预算）

总预算 **900 秒 / 64 次模型请求**（单案例单次运行）。**前 3 分钟必须先落盘结果骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求上限 | 步骤与输入/输出 |
|---|---|---|---|
| P0 落盘骨架 | 0–3 min | ≤6 | 输入 `/input`。执行 `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work`。输出 partial 骨架（case_id + 契约字段 + 空数组 + runtime）。**此步最先完成，绝不留空产物。** |
| P1 发现与读取 | 3–12 min | ≤28 | 扫描材料；`python3 -B scripts/litqa.py locators --work /app/work` 生成稳定段落/句子定位；识别研究问题与文献类型；记录检索范围与筛选依据。输出 `/app/work/` 文本/定位中间件 + `candidates`/`screening`/`sources`。 |
| P2 逐维度评价 | 12–21 min | ≤22 | 按 `references/rubric.md` 逐文献、逐维度、逐条目判定；每条带 `locator+quote` 并登记 `evidence_map`；`python3 -B scripts/litqa.py verify` 核验逐字匹配。输出填充 `assessment` 与 `evidence_map`。 |
| P3 分级与冲突 | 21–25 min | ≤6 | `python3 -B scripts/litqa.py grade` 聚合风险等级；汇总冲突、遗漏、不确定性、limitations。输出 `assessment.md` + 更新 `result.json`。 |
| P4 校验收尾 | 25–30 min | ≤2 | `python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work`；修正 error 级问题；设 `status` 最终值，写 `change_log`、`runtime.finish_epoch`。 |

原则：每阶段结束增量写盘（骨架 → 部分评价 → 完整评价）；中途异常不留空产物，`status` 保持可解释。

## 5. 自检清单

完整清单见 `references/checklist.md`，交付前逐条勾选。核心项：

- [ ] 产出"维度—判断—证据—等级"表，非摘要改写；覆盖四类维度 + 文献类型特有检查点。
- [ ] 每条 `quote` 逐字存在于所标 `locator`；`evidence_map` 含 `quote_sha256`，`verify` 抽查通过。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段、`status`/`sources`/`uncertainty`/`change_log`/`evidence_map`。
- [ ] `python3 -B scripts/main.py validate` 无 error 级问题；`scripts/selftest.py` 与 `main.py self-test` 通过（空目录/坏 JSON/缺输入退出码 0）。
- [ ] 全文不可得、仅摘要、图表不可解析、预印本/语言特殊、关键证据缺失等已如实标注，未编造。

## 6. 降级策略

- 缺数据：材料不足以支撑某维度时该条目判 `insufficient_evidence`，`status` 降为 `insufficient_evidence` 或 `partial`，`limitations` 写清缺口，不补造。
- 缺软件：仅依赖 Python 标准库；若 `pdftotext` 等工具不可用，保留原始 PDF 并在 `degradations` 记录"未抽取文本"，仅评可得部分。
- 冲突/权限/接口异常：写 `errors`/`pending_human`；候选集为空时如实 `status=insufficient_evidence` 并说明已完成与未完成部分。
- 重复运行：确定性流程（无随机数、固定定位与阈值），同一输入结果一致。

## 7. 脚本（在 SKILL 根目录以相对路径运行）

- `scripts/main.py`：`skeleton` 立即落盘骨架 / `validate` 结构校验 + 逐字核验 / `self-test` 三场景自检。
- `scripts/litqa.py`：`locators` 段落/句子定位 / `verify` 逐字核验 + 哈希 / `grade` 阈值聚合分级。
- `scripts/selftest.py`：包级自检（frontmatter、脚本可编译导入、自检通过、产物 schema）。
- 量表与检查清单见 `references/rubric.md`、`references/checklist.md`。

## 8. 最小调用示例（示例，非正式案例）

输入 `/input/case.json`（含 `case_id` 与 `output_contract`）与 `/input/task.md`：

```bash
# 1) 前三分钟内落盘骨架（最先执行）
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
# 2) 抽取文本后生成段落/句子定位
python3 -B scripts/litqa.py locators --work /app/work
# 3) 逐维度评价并写 result.json（assessment + evidence_map），随后核验引文
python3 -B scripts/litqa.py verify --input /input --work /app/work --output /app/output
# 4) 聚合风险等级并收尾
python3 -B scripts/litqa.py grade --output /app/output
python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
```

示意输出（节选，**标注为示例**，不含正式案例结果）：

```json
{
  "case_id": "case-example",
  "status": "partial",
  "assessment": [ {"id":"a1","dimension":"研究设计","checkpoint":"设计类型","critical":true,
    "verdict":"low_risk","judgment":"RCT，设计类型与问题匹配",
    "locator":{"file":"paper1","region":"fulltext","paragraph":2,"sentence":1},
    "quote":"This was a randomized controlled trial.","evidence_ids":["ev1"],"basis":"direct"} ],
  "evidence_map": [ {"evidence_id":"ev1","claim_id":"a1",
    "locator":{"file":"paper1","region":"fulltext","paragraph":2,"sentence":1},
    "quote":"This was a randomized controlled trial.",
    "quote_sha256":"f2fb45e1…","char_span":{"start":0,"end":38},
    "provenance":"fulltext","match":"verbatim","verified":true,"basis":"direct"} ]
}
```

