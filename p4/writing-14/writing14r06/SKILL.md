---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 task.md 指定的科研材料（事实底稿、课题指南、前期数据、文献、组内讨论）中抽取约束与已知结论，产出七要素齐全、问题—目标—路线—成果一一对齐、周期经费相容、风险带触发信号与备选方案、论断可逐条回溯的可复核开题方案；先落盘 plan.json 中间表示与 evidence_map.json 证据映射，再编译为 proposal.md / route.mmd / schedule.mmd / overview.svg 等可编辑产物，并写 /app/output/result.json（含题目 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是"开题报告与研究计划书"任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**七要素齐全、证据可回溯、可继续编辑**的开题方案或结构化写作产物时，按本 Skill 执行。

核心目标（对应评分四个重点）：
- **立得住**：研究问题具体、可证伪；背景材料真的指向缺口，而非综述一遍。
- **对得上**：目标、技术路线、验证方式、预期成果与问题一一对应，不各写各的。
- **排得开**：阶段计划落在给定周期、经费、数据与人员条件内，里程碑与工作量匹配。
- **说得清**：风险有触发信号与备选方案；关键论断与引用可追溯、可复核。

## 何时不使用
纯问答、无材料输入，或 task.md 明确要求其他体裁且与开题无关时，按 task.md 直接处理；本 Skill 的七要素/证据规范不强制套用。

# 输入契约

- 任务说明 `/input/task.md`（必读，先于任何假设）。
- 案例输入 `/input/case.json`：可能含 `case_id`、`output_contract`（或 `output_schema`/`required_fields`/`contract`）、材料路径、体裁/受众/格式约束、周期/经费/数据/人员条件。
- 科研材料按 case.json 与 task.md 指定路径读取真实文件；**PDF 用解析文本/OCR 读取，不假设标签内容**；解析层缺失时用运行时 PDF/OCR 工具。
- 目录：中间产物 `/app/work/`；最终产物与可编辑产物 `/app/output/`。
- 资源入口 `environment/resources.yaml`（若存在）：以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 全部字段**：从 case.json 提取字段名逐项填写；未声明时用默认字段 `title`、`summary`、`proposal`、`evidence_map`、`boundary`、`artifacts`。
3. 信封字段：`status`（`complete`/`partial`/`insufficient_evidence`/`failed`）、`sources`、`uncertainty`、`pending_human`、`change_log`。
4. 中间表示与可复核产物（本版重点）：
   - `plan.json`：结构化中间表示，含 `constraints` + 七要素 + 要素对齐，是唯一事实源。
   - `evidence_map.json`：`{entries: [{claim_id, claim, source_id, locator, quote, status}]}`，逐条回溯。
   - `scripts/main.py render` 由 `plan.json` 编译 `proposal.md`（七章节稿件）、`route.mmd`（路线流程图）、`schedule.mmd`（甘特图）、`overview.svg`（自包含预览）、`evidence_map.md`（证据溯源表）。
5. 题目要求 Office/slide 格式时，用运行时 `office-renderer`/`browser-automation` 生成并附真实渲染的 PDF/PNG 预览，路径写入 `artifacts`。

字段语义、七要素结构与词表见 `references/output-contract.md`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟先落盘合法 partial 骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。脚本调用统一用相对路径 `scripts/main.py ...`，在 SKILL.md 所在目录（Skill 根目录）执行；若当前目录不是 Skill 根目录，先定位 SKILL.md 再执行。

- **步骤 0（0–1 分钟）** 读 task.md 与 case.json，确定 `case_id`、材料路径、体裁/受众/格式、`output_contract`；落盘骨架：
  `python scripts/main.py init --case /input/case.json --out /app/output --work /app/work`
  → 产出 result.json（status=partial）、plan.json、evidence_map.json 三份骨架。
- **步骤 1（1–3 分钟）** 读全部材料（PDF 解析/OCR），建立 `sources` 清单（locator 页码/段落 + 逐字 quote）；把 `title`、`summary` 与初版 `sources` 回填 result.json。
- **步骤 2（3–6 分钟）** 抽取约束并写 `plan.json`：从课题指南/前期数据/文献/组内讨论提炼"已知—未知—待验证"缺口，收敛出 1–3 个可证伪问题，为每个问题写可考核目标（指标/样本/对照/时间点）、技术路线步骤（输入/输出/依赖/并行）、里程碑（月份/工作量）、风险（触发信号/影响/备选/决策时点）、预期成果（考核方式/目标）。每条关键论断写入 `evidence_map`，无据连接不写。
- **步骤 3（6–11 分钟）** `python scripts/main.py render ...` 编译可编辑产物与预览；如题目要求 docx/pptx，用运行时工具生成并渲染 PDF/PNG 预览。若 plan.json 尚未填完，render 仍会按已有内容产出合法骨架，保证不空手。
- **步骤 4（11–14 分钟）** 对照 `references/checklist.md` 逐条自检；材料缺口/冲突/推断写入 `uncertainty`，动作写入 `change_log`，确定 `status`；重跑 `check` 确认 FAIL=0。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：
- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全、与周期/经费/数据/人员相容；问题—目标—路线—成果对齐。
- [ ] 每条论断能定位到 sources（locator/quote）；evidence_map 的 source_id 均在 sources 中；fact/summary/inference 区分正确。
- [ ] 每条风险有触发信号/影响/备选/决策时点；未确认项入 pending_human。
- [ ] `check --strict` 退出码 0（FAIL=0）；`selftest.py` 输出 PASS 3/3。
- [ ] 前 3 分钟已落盘骨架；change_log 记录增量过程；plan.json 与 proposal.md 无漂移。
- [ ] 未编造分数、软件状态或科学数据。

# 降级策略

- **材料缺失/无法解析**：不补造；受影响要素留空并写 `uncertainty`（kind=missing），`status=partial`，在 summary 后说明 `limitations`。
- **信息自相矛盾**：并列两个版本标 `conflict` 并指出来源，建议人工裁决（`pending_human`），不擅自选一个。
- **格式/软件不支持**：优先交付 Markdown + Mermaid + SVG（纯标准库）；需 Office 而运行时工具不可用时，交付 Markdown 并注明"目标格式未生成，需运行时渲染工具"。
- **节点/字段爆炸**：只保留对问题/结论/任务直接相关的内容，其余写入 `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed` 或 `partial`，在 `limitations` 与 `change_log` 说明；**绝不编造评测分数或运行结果**。
- **budget 用尽**：以最近一次 `check` 通过的 partial 为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

```json
{
  "case_id": "demo",
  "output_contract": {
    "title": {"required": true},
    "proposal": {"required": true},
    "evidence_map": {"required": true}
  }
}
```

执行：`python scripts/main.py init && python scripts/main.py check && python scripts/main.py render`

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：某方向开题方案",
  "summary": "示意内容，非正式案例结果",
  "proposal": {"background": {"gap_statement": "待填"}, "problem_definition": {"questions": []},
    "objectives": [], "technical_route": {"steps": [], "critical_path": []},
    "schedule": {"cycle": "", "milestones": []}, "risks": [], "expected_outcomes": []},
  "evidence_map": [],
  "boundary": {"changes": [], "material_gaps": [], "exceptions": []},
  "artifacts": ["proposal.md", "plan.json", "evidence_map.json"],
  "sources": [],
  "uncertainty": [{"item": "result", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}]
}
```
