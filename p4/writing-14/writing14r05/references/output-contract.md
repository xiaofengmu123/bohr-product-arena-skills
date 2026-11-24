# 输出契约与字段语义（开题报告与研究计划书）

本文件定义 `result.json` 的完整字段、七要素/四问/证据链的结构，以及
`status` / `sources` / `uncertainty` / `pending_human` / `change_log` 的写法。
Agent 在写作时应以本文件为准；脚本 `scripts/main.py check` 据此校验。

## 1. result.json 顶层

```json
{
  "case_id": "与 case.json 的 case_id/id 一致",
  "status": "complete | partial | failed | insufficient_evidence | input_error",
  "<output_contract 字段>": "逐一对齐 case.json 中的 output_contract 键，缺失值为 null/[]",
  "title": "开题方案标题",
  "summary": "一句话摘要：做什么、为什么现在做、如何验证",
  "constraints": {},
  "seven_elements": {},
  "four_checks": {},
  "evidence_map": [],
  "artifacts": [],
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": []
}
```

- **output_contract 字段**：以 `case.json` 中 `output_contract` /
  `output_schema` / `required_fields` / `contract` / `schema` / `fields`
  同义键为准，**原样回填全部键**，不改名、不删未知键、缺值给 `null` 或 `[]`。
- **case_id**：优先 `case_id`，其次 `id` / `caseId` / `case`；不得为空。
- **status 语义**：
  - `complete`：七要素齐全、四问通过、证据链完整、产物落盘；
  - `partial`：交付了诚实但未全部完成的结果（缺材料、时间不足、某项待补）；
  - `failed`：运行失败或无法产出主结果；
  - `insufficient_evidence`：材料不足以支撑任何关键论断；
  - `input_error`：case.json 缺失、不可解析或契约字段不明。

## 2. constraints（输入约束抽取）

从课题指南、任务说明与材料中抽取，用于"排得开"校验：

```json
{
  "cycle": {"start": "YYYY-MM", "end": "YYYY-MM", "duration_months": null},
  "budget": "经费额度或 null",
  "data": "可获取的数据/样本条件或 null",
  "personnel": "人员/人月条件或 null",
  "audience": "受众（导师/评审/基金委等）",
  "genre": "体裁（开题报告/研究计划书/申请书）",
  "format": "docx / markdown / 其他",
  "discipline_norm": "学科规范（结构/引用/伦理）",
  "guideline_refs": ["课题指南/任务卡中可指认的约束出处"]
}
```

## 3. seven_elements（七要素）

对应评分项"研究背景、问题定义、目标、技术路线、阶段计划、风险、预期成果"。

```json
{
  "background_gap": {"known": [], "unknown": [], "to_verify": []},
  "problems": [
    {"question": "可证伪的研究问题", "falsifiable": "可证伪判据", "gap_ref": "对应缺口"}
  ],
  "objectives": [
    {"problem_id": "Q1", "goal": "可考核目标", "metric": "指标",
     "sample": "样本", "control": "对照", "timepoint": "时间点"}
  ],
  "technical_route": [
    {"step": "步骤名", "input": "输入", "output": "输出",
     "depends_on": [], "parallel_with": []}
  ],
  "stage_plan": {
    "milestones": ["按季度/月份排布的里程碑"],
    "critical_path": ["关键路径步骤"],
    "resources": ["资源需求（人员/经费/数据）"],
    "parallel": ["可并行部分"]
  },
  "risks": [
    {"category": "data_acquisition | method_feasibility | schedule | ethics_compliance",
     "risk": "风险描述", "trigger": "触发信号", "impact": "影响范围",
     "fallback": "备选方案", "decision_point": "决策时点"}
  ],
  "expected_outcomes": [
    {"outcome": "成果", "criterion": "考核方式", "linked_objective": "关联目标"}
  ]
}
```

- `problems` 建议 1–3 个；每个问题必须有 `falsifiable`（可证伪判据），
  且 `gap_ref` 指回 `background_gap.to_verify`，否则"立得住"不通过。
- `objectives` 的 `problem_id` 必须能对应到 `problems` 的 `question`/编号，
  否则"对得上"不通过。
- `stage_plan.milestones` 的时点必须落在 `constraints.cycle` 之内，
  且与 `resources` 相容，否则"排得开"不通过。

## 4. four_checks（四问自检）

```json
{
  "stand_up":        {"status": "pass|partial|fail", "note": "说明", "evidence": ["ev-01"]},
  "align":           {"status": "...", "note": "...", "evidence": []},
  "schedule_feasible":{"status": "...", "note": "...", "evidence": []},
  "traceable":       {"status": "...", "note": "...", "evidence": []}
}
```

四问与评分点对应：

| 键 | 含义 | 校验要点 |
|---|---|---|
| `stand_up` | 立得住 | 问题具体、可证伪；背景真正指向缺口，而非文献综述 |
| `align` | 对得上 | 目标↔路线↔验证↔成果与问题一一对应 |
| `schedule_feasible` | 排得开 | 阶段计划落在周期/经费/数据/人员内，里程碑与任务量匹配 |
| `traceable` | 说得清 | 风险有触发信号与备选方案；关键论断与引用可回溯 |

## 5. evidence_map（证据链）

每个关键数字、术语、引用、图表与论断都要有一条记录：

```json
{"id": "ev-01", "claim": "论断原文", "locator": "材料定位",
 "quote": "逐字引用", "kind": "fact|summary|inference", "target": "支撑的要素/对象"}
```

- `kind` 语义：
  - `fact`：材料原文可指认（带 locator + 逐字 quote）；
  - `summary`：多段归纳（给 locator 范围）；
  - `inference`：Agent 补出的推断（**必须**写入 `uncertainty`）；
  - `conflict`：两来源不一致，并列呈现、建议人工裁决；
  - `missing`：材料缺失，不得补造。
- `locator` 示例：`materials/paper.pdf#p3`、`notes.md#L12`、`guideline.md#§2`。
- 论断与引用必须**一一对应**：正文中出现的每个数字/引用/指标阈值，
  都应在 `evidence_map` 有对应条目；反过来 evidence_map 不得引用材料外事实。

## 6. sources（来源清单）

```json
{"id": "src-01", "path": "materials/paper.pdf", "type": "pdf|md|docx|other",
 "pages": "1-5", "note": "材料说明"}
```

`evidence_map` 中的 `locator` 应能定位到 `sources` 里的文件；二者要一致。

## 7. uncertainty / pending_human

```json
{"item": "result", "issue": "描述", "kind": "missing|conflict|inference|out_of_scope|limitation", "action": "建议动作"}
```

```json
{"item": "指标阈值", "question": "需人工确认的问题"}
```

- 材料缺口、信息冲突、Agent 推断、超出范围的内容都要写入 `uncertainty`，
  不得静默省略或擅自选一边。
- 需导师/评审裁决的内容写入 `pending_human`。

## 8. change_log（修改记录）

```json
{"at": "ISO-8601 时间", "action": "init|fill|check|build|degrade|finalize",
 "reason": "本次修改原因", "source": "相关文件或 null"}
```

必须能还原"从骨架到成品"的增量过程；第一版骨架在开始 3 分钟内落盘。

## 9. artifacts（可编辑产物）

`build` 会生成（相对 `/app/output/` 的路径）：

| 文件 | 说明 |
|---|---|
| `proposal.md` | 七要素齐全、带章节编号的可编辑开题方案 |
| `evidence_map.md` | 逐条可回溯的证据映射表 |
| `checks.md` | 四问自检报告 |
| `proposal.docx` | 由 proposal.md 用标准库生成的 OOXML 文档（可编辑） |

任务要求 docx/pptx 且运行时 `office-renderer` 可用时，用该工具把
`proposal.docx` 渲染为 PDF/PNG 预览并把路径写入 `artifacts`；工具不可用时
如实降级并在 `uncertainty`（kind=limitation）说明。
