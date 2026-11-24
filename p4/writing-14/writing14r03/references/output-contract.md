# 输出契约与字段词表

本文件定义 `result.json` 的字段语义、七要素子字段、source 结构与风险词表。完整字段与词表以本文件为准；与 `SKILL.md` 冲突时以本文件为细目。

## 1. result.json 顶层

| 字段 | 必填 | 语义 |
|---|---|---|
| `case_id` | 是 | 与 case.json 的 `case_id`/`id` 一致 |
| `status` | 是 | `complete` / `partial` / `failed` / `insufficient_evidence` / `input_error` |
| `title` | 是 | 方案标题 |
| `summary` | 是 | 一段话概述研究问题、方法与预期价值 |
| `limitations` | 否 | 边界与限制的总体说明（材料缺口/目标格式未生成等） |
| 七要素 | 是 | 见第 2 节：`background`、`problems`、`objectives`、`methods`、`plan`、`risks`、`expected_outcomes` |
| `references` | 是 | 关键论断与引用的一一映射（claim ↔ source_id） |
| `boundaries` | 否 | `in_scope` / `out_of_scope` 数组 |
| `artifacts` | 是 | 产物绝对路径数组（proposal.md、evidence.md 及预览） |
| `sources` | 是 | 证据清单，每项见第 3 节 |
| `uncertainty` | 是 | 不确定/待确认项数组 |
| `pending_human` | 是 | 需人工确认项数组 |
| `change_log` | 是 | 修改记录数组 |
| `errors` / `degradations` | 否 | 运行异常与降级记录 |

- 题目 `output_contract`（或 `output_schema`/`required_fields`/`contract` 等同义键）声明的字段**必须原样回填全部键名**；缺值给 `null`/`[]`，不改名、不删未知键。
- 契约未声明时，使用默认字段 `title`、`summary`、`proposal`、`artifacts`。

## 2. 七要素子字段（对应四问：立得住/对得上/排得开/说得清）

### 2.1 background 研究背景与缺口（立得住）
- `known`：材料可直接指认的已知结论（数组，逐条给 source_id）。
- `gap`：由约束与已知结论推出的缺口陈述，必须具体、指向待验证问题，而非领域综述。
- `to_verify`：本课题要验证的命题。

### 2.2 problems 研究问题（立得住）
1–3 个；每个含：
- `id`：问题标识（如 P1）。
- `question`：可回答、可证伪的研究问题。
- `falsifiable`：可证伪判据（什么证据会推翻它）。
- `gap_link`：指向 background 的缺口（如 G1）。

### 2.3 objectives 目标与考核标准（对得上）
每个目标与问题一一对应：
- `problem_ref`：指向 problems 的 `id`。
- `goal`：目标陈述。
- `criteria`：可考核判断标准，**必须含指标、样本、对照、时间点**。

### 2.4 methods 技术路线（对得上）
拆成有输入输出与前后依赖的步骤；每个含：
- `step`：步骤名。
- `input` / `output`：输入与产出。
- `depends_on`：前置步骤（数组），标清关键路径与可并行部分。

### 2.5 plan 阶段计划（排得开）
- `period`：总周期；`budget`：经费口径。
- `milestones`：按季度/月份排布的里程碑，每项含任务、时点、对应 method 步骤、资源需求。
- `critical_path`：关键路径；`parallelizable`：可并行步骤。
- 里程碑必须落在给定周期、经费、数据与人员条件内。

### 2.6 risks 风险与备选方案（说得清）
按 `category` 分类，词表：`数据获取`、`方法可行性`、`进度`、`伦理与合规`、`资源`。
每条风险必须含：
- `trigger`：触发信号。
- `impact`：影响范围。
- `alternative`：备选方案。
- `decision_point`：决策时点。
- 另可加 `source_id` 追溯风险判断依据。

### 2.7 expected_outcomes 预期成果（说得清）
- `outcome`：成果描述（论文/数据/方法/系统等）。
- `verification`：考核方式（如何证明成果达成）。

## 3. source 结构（证据溯源）

每项 source 对象：
- `id`：来源标识（S1、S2…）。
- `locator`：定位（如 `材料.md#p3`、`指南.md#第2节`、`paper.pdf#第4页`）。
- `quote`：逐字原文片段（关键数字/术语/论断必须逐字引用）。
- `claim`：该来源支持的论断摘要。
- `kind`：`fact`（原文可指认）/ `summary`（多段归纳）/ `inference`（Agent 补出）。
- `conflict_with`：若与另一来源矛盾，指向冲突来源 id（配合 `uncertainty.kind=source_conflict`）。

规则：`fact`/`summary` 节点与关系的每个关键论断都要能指回 sources；`inference` 项必须同时列入 `uncertainty`。

## 4. uncertainty / pending_human / change_log

- `uncertainty` 每项：`item`（受影响对象）、`issue`（说明）、`kind`（`missing`/`conflict`/`source_conflict`/`out_of_scope`/`inference`）、`action`（建议动作）。
- `pending_human` 每项：`item`、`why`（为何需人工）、`action`（建议的人工决策）。
- `change_log` 每项：`at`（ISO 时间）、`action`（动作）、`reason`（原因）；应能还原"从骨架到成品"的增量过程。

## 5. 确定性规则

- 渲染产物（proposal.md/evidence.md）**不得写入墙钟时间、随机数或文件生成时间**；相同 result.json 两次渲染必须逐字节一致。
- `init`/`render` 使用固定键序与固定插入顺序写 JSON，不使用 `sort_keys` 以外的非确定性输入。
- `check` 只读输入、不改 result.json；`render` 只在 `artifacts` 中追加缺失路径，不重排既有顺序。
- 证据 `quote` 必须逐字，不得转写；确无逐字原文时写 `pending_human` 或 `not_reported`，不伪造。
