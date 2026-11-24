# 四项交付判定口径与证据字段规范

本文定义本 Skill 中四类判断的判定口径、证据字段与状态取值。Agent 在 Phase 2/3 写 fragment 时按此口径执行；每条关键结论都必须在 `evidence_matrix` 回链到 `locator + 逐字 quote`。

## 1. 研究问题定义（research_question）

把模糊研究设想拆成可检验陈述，字段：

| 字段 | 含义 | 缺失处理 |
|---|---|---|
| `subject` | 研究对象/人群/系统 | `status=missing` 并列缺口 |
| `variables` | 关键变量（自变量/因变量/协变量） | 同上 |
| `baseline` | 比较基线（对照、前作、基准模型） | 同上 |
| `expected_outcome` | 预期结论（方向与幅度，不夸大） | 同上 |
| `falsification` | 可证伪条件（什么观察会推翻结论） | 同上 |
| `testable_statement` | 一句话可检验陈述 | 由上述字段合成，不得无中生有 |
| `status` | `complete / partial / missing` | 诚实标注 |

判定：`testable_statement` 同时含对象/变量/基线/可证伪条件才算可检验；缺项写 `gaps`，不要把缺口写成“已知”。

## 2. 已有工作比较（prior_work）

按统一维度建比较矩阵，字段：`task`（任务）、`method`（方法）、`data`（数据）、`evaluation`（评价方式）、`conclusion`（结论）、`comparability`（可比性/不可比原因）。

规则：
- 只比较材料中真实出现的工作，禁止跨文档串值（例如把 A 的方法接到 B 的数据上）。
- 每项比较写明 `source_id + locator + quote`；无法比较要写清原因（数据口径、人群、评价指标不同等）。
- “检索不到”不等于“尚无人研究”，只能写 `not_reported`，不得断言 novelty。

## 3. 创新点识别（novelty）

先分类，再逐条判覆盖：

| 类型 | 判定要点 |
|---|---|
| `method` 方法创新 | 新机制/新算法/新流程；需材料证明其未被前作提出 |
| `data` 数据创新 | 新数据集/新标注/新测量；需材料证明其规模或维度增量 |
| `scenario_transfer` 场景迁移 | 已有方法换到新场景；**不得**记为方法创新 |
| `conclusion_revision` 结论修正 | 推翻/修正前作结论；需给出对立证据 |

每条输出 `verdict ∈ novel | covered | partially_covered | unverifiable`，并给出：
- `claim`：创新陈述原文（quote）
- `covered_by`：被哪项已有工作覆盖（source_id + locator）
- `reason`：判定理由（可回查）
- `boundary`：该创新相对已有方案的边界与价值

场景迁移/结论修正没有被材料支持为方法创新时，`verdict` 不得写 `novel`。

## 4. 可行性判断（feasibility）

核查前提：数据、算力、实验条件、时间窗口、人员能力。每条前提标状态：

| 状态 | 含义 |
|---|---|
| `reported` | 材料中有明确来源，附 locator+quote |
| `assumed` | 推断/假设，显式标注，不写成事实 |
| `missing` | 材料未报告，写入 `uncertainty.gaps` |
| `conflicting` | 不同来源结论相反，进 `conflicts` |

输出：带条件的可行性结论（`feasible_if` / `not_feasible_because`）+ 失败风险列表（`risks`）。每个前提都要有出处；assumed 必须显式标注。

## 5. 证据字段规范

`evidence_matrix` 每条：

```json
{
  "claim": "被支持或被打分的判断",
  "source_id": "materials/note.md",
  "locator": "materials/note.md#L3",
  "quote": "材料中的逐字原文",
  "field": "novelty|feasibility|prior_work|research_question",
  "status": "reported|inferred|not_reported|conflicting"
}
```

- `quote` 必须逐字，禁止改写或补齐；没有可引用片段写 `not_reported`，并把条目记入 `pending_human`。
- `locator` 规范：文本材料用 `<source_id>#L<行号>`；PDF 用抽取文本 `<source_id>.txt#L<行号>`，或能确定的页号 `#p<页号>`。
- 冲突证据一律进 `conflicts`：`topic`（争议点）、两侧记录（各带 source_id/locator/quote）、`resolution`（取舍理由）。不隐藏反面证据。

## 6. 状态取值与边界

| status | 触发条件 |
|---|---|
| `ok` | 四项交付齐全、证据可核验、无未解决冲突 |
| `partial` | 有部分交付或部分证据，但未齐或存疑 |
| `insufficient_evidence` | 有输入但无可读材料，无法支撑结论 |
| `input_error` | 输入目录缺失、case.json 缺失或无法解析 |

综合结论只到证据范围为止（`synthesis.boundary_statement`）；不确定结论标 `inferred`，不把推断写成事实，不编造 DOI、数值或运行结果。
