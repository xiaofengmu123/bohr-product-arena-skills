# 输出契约与字段词表

本文件定义 `result.json` 的字段语义、七要素 schema、来源结构与状态词表，供他人按此复用与复核。SKILL.md 与本文件冲突时以本文件为准。

## 1. 信封字段（每个案例必填）

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 与 case.json 的 `case_id`/`id` 一致 |
| `status` | string | `complete` / `partial` / `insufficient_evidence` / `input_error` / `failed` |
| `title` | string\|null | 方案标题 |
| `summary` | string\|null | 一段话摘要（含 limitations 说明） |
| `audience` | string\|null | 受众（导师/评审/基金委等） |
| `genre` | string\|null | 体裁（开题报告/研究计划书等） |
| `format_constraints` | array | 格式约束（markdown/docx/pptx 等） |
| `constraints` | object | `period`/`budget`/`personnel`/`data`，缺失给 `null` |
| `sources` | array | 证据清单（见 §3） |
| `uncertainty` | array | 不确定/冲突/推断/缺口项（见 §4） |
| `pending_human` | array | 需人工确认项 |
| `change_log` | array | `{at, action, note}` 修改记录 |
| `errors` / `degradations` / `limitations` | array | 输入错误、能力降级、边界说明 |
| `runtime` | object | 执行元信息（引擎、步骤、时间、契约字段） |
| `artifacts` | array | 产物相对路径（可编辑文件 + 预览） |

`status` 判定：七要素齐、无 FAIL、无 pending_human、缺口已闭合 → `complete`；有缺口/推断/待确认 → `partial`；无材料可分析 → `insufficient_evidence`；输入不可读 → `input_error`；运行异常 → `failed`。**存在 pending_human 或材料缺口时不得标 complete。**

## 2. 七要素 schema（`proposal` 字段）

### 2.1 研究背景与缺口 `background_gap`
```json
{
  "known":   [{"id", "text", "kind", "sources": ["S1"]}],
  "unknown": [{"id", "text", "kind", "sources": ["S1"]}],
  "to_verify":[{"id", "text", "kind", "sources": []}],
  "gap_statement": {"text", "kind", "sources": ["S1"]}
}
```
缺口陈述必须由已知—未知—待验证推出，指向明确缺口，而非文献罗列。

### 2.2 研究问题 `research_questions`（1–3 个，可证伪）
```json
{"id": "RQ1", "question": "...", "falsifiable_criterion": "...", "kind": "...", "sources": ["S1"]}
```

### 2.3 目标 `objectives`（含可考核口径，挂 linked_question）
```json
{"id": "OBJ1", "objective": "...", "metric": "...", "threshold": "...",
 "sample": "...", "control": "...", "timepoint": "...", "verification": "...",
 "linked_question": "RQ1", "kind": "...", "sources": ["S1"]}
```
`threshold` 无依据时给 `null` 并标 `kind: inference`、写入 uncertainty。

### 2.4 技术路线 `technical_route`（每步有输入输出与依赖）
```json
{"id": "ST1", "name": "...", "input": "...", "output": "...",
 "depends_on": [], "parallel_with": ["ST2"], "kind": "...", "sources": ["S1"]}
```

### 2.5 阶段计划 `phase_plan`（映射周期/经费/人员）
```json
{"id": "P1", "phase": "Q1", "duration": "3 个月", "milestones": ["M1"],
 "key_path": true, "resources": "1 人", "linked_steps": ["ST1"]}
```

### 2.6 风险 `risks`（分类 + 触发信号 + 备选）
```json
{"id": "R1", "category": "data", "risk": "...", "trigger_signal": "...",
 "impact": "...", "fallback": "...", "decision_point": "...",
 "kind": "...", "sources": []}
```
`category` 词表：`data`（数据获取）/ `method`（方法可行性）/ `schedule`（进度）/ `ethics`（伦理）/ `compliance`（合规）/ `other`。

### 2.7 预期成果 `expected_outcomes`（含考核方式）
```json
{"id": "EO1", "outcome": "...", "verification_method": "...", "linked_objective": "OBJ1"}
```

## 3. 来源结构 `sources`

```json
{"id": "S1", "locator": "materials/paper.md#p3", "quote": "原文逐字段落",
 "kind": "fact|summary|inference|conflict", "title": "文献标题（可选）"}
```
`locator` 优先 `文件路径#页码` 或 `#段落`；`quote` 为**逐字**片段，不转述。每条来源至少含 locator 或 quote 之一。

## 4. 不确定项 `uncertainty`

```json
{"item": "OBJ1.threshold", "issue": "材料未给阈值", "kind": "missing|conflict|inference|out_of_scope", "action": "人工确认"}
```
`kind` 词表：`missing`（材料缺失）、`conflict`（信息冲突）、`inference`（Agent 推断）、`out_of_scope`（超出范围）。**所有 `kind: inference` 的七要素项必须在此有对应记录。**

## 5. 证据映射 `evidence_map`

由 `render` 自动生成，把七要素项与来源逐条挂接：`{claim_id, kind, source_id, locator, quote, source_title}`。评分人可按此逐条复跑核对。

## 6. 产物

| 文件 | 用途 |
|-|-|
| `proposal.md` | 主可编辑稿件（七要素 + 来源标注 + 缺口附录） |
| `evidence_map.json` | 论断↔来源映射，可机器核验 |
| `proposal_preview.html` | 自包含 HTML 预览，无需额外软件 |
| docx/pptx + PNG/PDF 预览 | 题目要求时用运行时 office-renderer 生成并追加进 artifacts |
