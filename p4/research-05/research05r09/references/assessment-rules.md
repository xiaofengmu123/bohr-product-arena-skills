# 评估口径：五项交付与证据字段

本文档定义 SKILL.md 第 4 节五项交付的判定口径。Agent 写 fragment 时按此口径落字段，评审按此口径复核。

## 1. 研究问题定义（research_question）

目标：把模糊设想拆成可检验陈述。字段：
- `statement`：原始研究设想（尽量逐字取自材料，含 locator）。
- `object`：研究对象；`variables`：关键变量（自变量/因变量/混杂）。
- `baseline`：比较基线；`expected_conclusion`：预期结论。
- `falsifiable_condition`：可证伪条件（什么结果会否定该设想）。
- `testable_statement`：把以上拼成一句话的可检验陈述。
- `status`：`complete|partial|missing`；缺项写入 `gaps`。

判定：`testable_statement` 缺对象/变量/基线/可证伪条件任一 → `partial`；全缺 → `missing`。

## 2. 检索协议（search_protocol）

目标：让检索可被独立复现。字段：`queries`（实际检索式）、`source_scope`（来源/数据库/时间范围）、`inclusion` / `exclusion`（纳排规则）、`selection_rationale`（筛选依据）、`coverage_note`（检索盲区，若已知）。

## 3. 已有工作比较（prior_work）

目标：识别真正相近工作，建立可比较矩阵。每行含 `task`、`method`、`data`、`evaluation`、`conclusion`、`source_id`、`locator`、`quote`。另含 `incomparable_reasons`（无法直接比较的原因）。禁止跨文档串值。

## 4. 创新点识别（novelty）

每条创新陈述字段：`claim`、`type`（`method|data|scenario_transfer|conclusion_revision`）、`verdict`（`novel|covered|partially_covered|unverifiable`）、`covered_by`（被哪条已有工作覆盖，含 source_id+locator）、`reason`、`evidence`（locator+quote）。

判定：场景迁移/结论修正不得判为方法创新；`unverifiable` 只用于材料不足以判定时，且注明缺什么。

## 5. 可行性判断（feasibility）

每个前提字段：`premise`、`evidence_status`（`reported|assumed|missing|conflicting`）、`evidence`（locator+quote，若 reported/conflicting）、`impact`（该前提失败的影响）、`risk`。输出 `conditional_conclusion`（带条件结论）与 `blockers`。`assumed` 必须显式标注为假设，不得当事实。

## 6. 证据字段规范

每条 evidence 记录至少：`source_id`、`locator`（`rel_path#L行号` 或 `rel_path#p页码`）、`quote`（逐字）。无引用信息写 `pending_human` 或 `not_reported`，禁止编造 DOI/数值/页码/来源。
