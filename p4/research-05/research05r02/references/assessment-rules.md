# 四项交付的判定口径与证据字段

本文件是 Skill 的分析口径，供执行者与复核者使用。核心原则：**所有结论必须落到材料的 locator + 逐字 quote；没有出处就标 `pending_human` 或 `not_reported`，不编造 DOI、页码、数值或运行分数。**

locator 形式：`task.md#L3`、`case.json#L5`、`materials/paper1.txt#L12`、`materials/paper.pdf#p2`（PDF 页码，对应 `work/extracted/*.pages.txt` 中 `===== PAGE n =====` 段）。逐字 quote 必须原文照抄，不得改写、不得补字。

---

## 1. 研究问题定义（research_question）

目标：把模糊设想拆成**可检验陈述**，而不是复述任务名。

必填子字段：

| 字段 | 含义 | 缺失时的处置 |
|---|---|---|
| `object` | 研究对象（人群/系统/模型/数据集） | 标 `missing`，写入 `uncertainty.gaps` |
| `variables` | 关键自变量与因变量 | 同上 |
| `baseline` | 比较基线/对照条件 | 同上 |
| `expected_outcome` | 预期结论的方向 | 同上 |
| `falsifiable_condition` | 什么观测会推翻该结论 | 同上 |
| `testable_statement` | 一句话可检验表述 | 缺则该项判 `partial` |

判定锚点：

- **完成**：`testable_statement` 同时包含对象、变量、基线、可证伪条件，且五个子字段均有出处或明确标 `missing`。
- **部分**：缺任一子字段，但 `testable_statement` 仍可读 → `status=partial`，逐项列缺口。
- **不可判**：材料完全没有研究对象信息 → 该项保持 `null`，写 `uncertainty.gaps`。

反例（判为不合格）：把“本研究将探索 X 与 Y 的关系”当作可检验表述——没有基线、没有可证伪条件。

---

## 2. 已有工作比较（prior_work）

目标：识别**真正相近**的工作，逐列比较；禁止跨文档拼接数值（例如把 A 的方法接 B 的数据拼成一条结论）。

比较矩阵字段：`task`（任务）、`method`（方法）、`data`（数据/对象）、`evaluation`（评价方式）、`conclusion`（结论）。

| 情形 | 处置 |
|---|---|
| 任务相同、方法不同 | 明确列出差异点（不是只写“不同”） |
| 方法相同、数据不同 | 标 `not_directly_comparable` 并写原因 |
| 数据/评价缺失 | 写 `not_reported`，不编数值 |
| 结论相左 | 进 `conflicts`，两方都登记，写明取舍 |

比较对象选择锚点：

- 优先选**任务相同或高度重叠**的工作；只“主题相关”的不算相近。
- 每条相近工作都要有 `source_id` 与 `locator` 回链。
- 找不到相近工作时写“在给定材料范围内未发现”，**不得**写“尚无人研究”。

---

## 3. 创新点识别（novelty）

每条创新陈述必须分类并判覆盖：

| `kind` | 定义 | 常见误判 |
|---|---|---|
| `method` | 方法/模型本身的新贡献 | 把换数据集说成方法创新 |
| `data` | 新数据集/新标注/新基准 | 把已公开数据当新数据 |
| `scenario_transfer` | 方法迁移到新场景 | 把应用迁移说成理论创新 |
| `conclusion_revision` | 修正既有结论 | 把推翻自己假设当领域新结论 |

`verdict` 判定树：

1. 材料中已有工作明确做到同一方法+同一数据+同一任务 → `covered`。
2. 已有工作做到核心方法，但数据/场景不同 → `partially_covered`，写明被哪篇工作覆盖（`covered_by`）。
3. 材料范围内无任何工作覆盖该方法贡献 → `novel`。
4. 材料不足以判定 → `unverifiable`，写 `uncertainty.unresolved`。

每条必须给 `covered_by`（被哪篇工作覆盖，可为 `null`）+ `reason` + 证据。场景迁移/结论修正**不得**判为方法创新，这是创新增益的关键扣分点。

---

## 4. 可行性判断（feasibility）

核查五类前提：**数据、算力、实验条件、时间窗口、人员能力**，逐条标注来源状态：

| 标签 | 含义 | 示例 |
|---|---|---|
| `reported` | 材料中有明确出处 | “数据来自公开数据集 D（materials/x.txt#L4）” |
| `assumed` | 执行者做的假设，必须显式标注 | “假设实验平台可复用现有设备（材料未报告）” |
| `missing` | 材料未报告 | “人员能力未报告” |
| `conflicting` | 材料间互相矛盾 | “材料 A 与 B 对时间窗口说法不一致”，进 `conflicts` |

结论必须带条件（“若 X 成立，则可行”）与失败风险；`assumed` 不得写成事实。

失败风险锚点（在 `conclusion` 中说明）：

- 关键前提为 `missing` → 高风险，不能下“可行”结论。
- 关键前提为 `assumed` → 中风险，结论带“若假设不成立则失败”。
- 全部前提 `reported` 且一致 → 才可下“条件可行”。

---

## 5. 检索协议（search_protocol）

用于证明“检索覆盖”可复核，字段：

- `query`：实际使用的检索式/关键词。
- `scope`：材料范围（如“仅给定材料”或“给定材料 + 检索到的公开文献”）。
- `inclusion`：纳入规则。
- `exclusion`：排除规则。
- `databases`：若使用外部检索，写来源；仅给定材料则写 `given_materials_only`。

规则：检索式与纳排规则必须具体到能独立复现；“检索不到”只写“给定范围内未发现”，不推断为“领域空白”。

---

## 6. 证据字段（evidence_matrix 条目）

| 字段 | 要求 |
|---|---|
| `source_id` | 与 `sources` 一致 |
| `locator` | `task.md#L3` / `materials/x.txt#L12` / `materials/x.pdf#p2` |
| `quote` | 逐字照抄原文片段，不得改写 |
| `field` | 该证据支持的字段路径（如 `novelty.claims[0]`） |
| `status` | `supporting` / `conflicting` / `context` |

每条**关键结论**都必须有对应条目；冲突证据两条都登记，不能只留支持性证据。

---

## 7. 状态裁决与推断标注

`status` 取值由客观条件决定，不由“写得多少”决定：

| 条件 | status |
|---|---|
| 输入目录缺失，或 `case.json` 缺失且无法回退 | `input_error` |
| 输入存在但无任何可读文本/PDF 材料 | `insufficient_evidence` |
| 材料可读，但四项交付或证据矩阵未全部完成 | `partial` |
| 四项交付齐全、证据矩阵非空、契约校验通过 | `ok` |

事实与推断分三层标注：

- `reported`：材料原文明确给出，附 locator + 逐字 quote。
- `inferred`：由材料推出来的合理判断，必须在结论中写明“推断自……”。
- `not_reported` / `pending_human`：材料没有，绝不补造。

“未报告”不等于“不存在”；“检索不到”不等于“尚无人研究”。这两条是诚实性的硬边界。

---

## 8. 边界与诚实性

- 结论止步于材料证据范围；跨文档推断要标 `inferred`。
- “检索不到”≠“尚无人研究”，只能写“在给定材料范围内未发现”。
- 不做全局完备性或新颖性声明；不补造 DOI、页码、数值或运行分数。
- `status` 与 `errors/degradations/pending_human/limits` 必须一致：材料不足就降级，不要把推断写成事实。

---

## 9. 常见失败模式与处置

题目明确点名的四类失败，本 Skill 逐条设防：

| 失败模式 | 正确处置 |
|---|---|
| 四平八稳综述式答复，读起来完整但不支撑决策 | 强制四项交付结构 + 每项落到可决策结论 |
| 把“检索不到”说成“尚无人研究” | 只能写“在给定材料范围内未发现”，进 `research_gaps` |
| 把方法上的旧方案当新方法、把应用场景迁移说成理论创新 | novelty 强制 `kind` 分类 + `covered_by` 覆盖判定 |
| 可行性只给一句“技术路线可行” | feasibility 强制五类前提逐条 `reported|assumed|missing|conflicting` |

额外守则：

- 禁止跨文档拼接数值：比较矩阵一行必须来自同一来源。
- 相反证据必须进 `conflicts` 并写明取舍，不能只报告支持性证据。
- 假设（`assumed`）必须显式标注，不得伪装成 `reported`。
- 推断（`inferred`）必须与原文事实（`reported`）分开写。

---

## 10. 模板示例（仅示意结构，非正式案例）

以下 JSON 只展示字段形状，正式运行时以 `/input` 真实材料为准。

### 10.1 五个 fragment 的落盘拆分（与 result.json 键一致）

为避免一次性写大 JSON 出错，把交付拆成五个小文件，每个只含一个顶层键，写进 `/app/work/fragments/`。文件名与顶层键对应，finalize 会按字典序合并。

| 文件 | 顶层键 | 必须包含 |
|---|---|---|
| `01_question.json` | `research_question` | object / variables / baseline / expected_outcome / falsifiable_condition / testable_statement |
| `02_prior.json` | `prior_work` | matrix 数组，每条含 task/method/data/evaluation/conclusion/comparability |
| `03_novelty.json` | `novelty` | claims 数组，每条含 claim/kind/verdict/covered_by/reason/evidence |
| `04_feasibility.json` | `feasibility` | premises 数组（五类前提各一条）+ conclusion + failure_risk |
| `05_synthesis.json` | `evidence_matrix` / `conflicts` / `synthesis` / `uncertainty` / `pending_human` | 关键判断回链 + 冲突 + 综合 + 空白 |

每个 fragment 只放对应顶层键的内容；`05_synthesis.json` 允许同时包含多个顶层键，因为综合阶段需要把证据矩阵与冲突一并回填。写完一个就落盘一个，剩余时间不够时 finalize 仍能把已写 fragment 合并成合法结果。

---

```json
{
  "research_question": {
    "object": "研究对象",
    "variables": ["自变量", "因变量"],
    "baseline": "对照基线",
    "expected_outcome": "预期方向",
    "falsifiable_condition": "何种观测会推翻结论",
    "testable_statement": "在【对象】上比较【变量】相对【基线】的差异，若【可证伪条件】则不成立",
    "status": "complete",
    "gaps": []
  },
  "prior_work": {
    "matrix": [
      {"work": "作者/材料标识", "task": "任务", "method": "方法", "data": "数据", "evaluation": "评价方式", "conclusion": "结论", "comparability": "direct|not_directly_comparable", "note": "不可比原因"}
    ]
  },
  "novelty": {
    "claims": [
      {"claim": "创新陈述", "kind": "method|data|scenario_transfer|conclusion_revision", "verdict": "novel|covered|partially_covered|unverifiable", "covered_by": "被覆盖来源或 null", "reason": "理由", "evidence": [{"source_id": "…", "locator": "…", "quote": "…", "field": "novelty.claims[0]", "status": "supporting"}]}
    ]
  },
  "feasibility": {
    "premises": [
      {"name": "数据|算力|实验条件|时间窗口|人员能力", "status": "reported|assumed|missing|conflicting", "evidence": [{"source_id": "…", "locator": "…", "quote": "…"}], "note": "假设说明"}
    ],
    "conclusion": "带条件的可行结论",
    "failure_risk": "风险等级与触发条件"
  },
  "search_protocol": {
    "query": "检索式", "scope": "given_materials_only", "inclusion": "纳入规则", "exclusion": "排除规则", "databases": []
  },
  "synthesis": {
    "conclusions": ["结论（止步于证据）"],
    "research_gaps": ["研究空白"],
    "boundary_statement": "边界说明"
  }
}
```
