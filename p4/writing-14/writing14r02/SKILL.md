---
name: weekly-skill
description: 开题报告与研究计划书撰写 Skill。从 /input/case.json 与 task.md 给定的课题指南、前期数据、文献与约束中抽取七要素（研究背景与缺口、研究问题、目标、技术路线、阶段计划、风险、预期成果），完成“问题↔目标↔路线↔验证↔成果”要素对齐与“周期/经费/指标”计划可行性校验，生成可编辑 markdown、证据映射（locator+逐字 quote）、Mermaid 甘特图与自包含 SVG 预览，并产出 /app/output/result.json（含 case_id、output_contract 全部字段与 status/sources/uncertainty/pending_human/change_log）。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是“开题报告与研究计划书”任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**可复核、可编辑、证据可追溯**的开题方案或结构化写作产物时，按本 Skill 执行。

核心目标对应题目“立得住、对得上、排得开、说得清”四点：**立得住**——研究问题具体、可证伪，背景只指向缺口而非文献综述；**对得上**——目标/技术路线/验证方式/预期成果与问题一一对应；**排得开**——阶段计划落在给定周期、经费、数据与人员条件内；**说得清**——风险有触发信号与备选方案，关键论断与引用一一对应、可逐条回溯。

## 何时不使用
纯问答、无材料输入、或 task.md 明确要求与本任务无关的格式时，按 task.md 直接处理；本 Skill 的七要素/证据/校验规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，可能含 `case_id`、`output_contract`（或 `output_schema`/`required_fields`/`contract`/`fields` 等同义键）、材料路径、体裁/受众/格式/周期/经费/数据/人员约束。
- 科研材料：按 case.json 与 task.md 指定路径读取真实文件。**PDF 用解析文本/OCR 读取，不假设标签内容**；无解析层时用运行时 pdf-text-tool/ocr-tool。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在），声明的预装工具以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 的全部字段**：优先从 case.json 提取字段名并逐一填写（不改名、不删未知键，缺值给 `null`/`[]`）；契约未声明时使用默认字段 `title`、`summary`、`genre`、`audience`、`format`、`constraints`、`seven_elements`、`evidence_map`、`artifacts`（详见 `references/output-contract.md`）。
3. 信封字段：`status`（`complete`/`partial`/`insufficient_evidence`/`input_error`/`failed`）、`sources`（材料清单）、`uncertainty`（不确定/待确认项）、`pending_human`（须人工裁决项）、`change_log`（修改记录）。
4. 可编辑产物：`scripts/proposal.py render` 生成 `proposal.md`（七要素完整方案）、`evidence_map.md`（证据映射表）、`schedule.mmd`（Mermaid 甘特图）、`preview.svg`（自包含预览）；题目要求 Office 格式时，用运行时 office-renderer 生成并附真实渲染的 PDF/PNG 预览。所有产物路径写入 `artifacts`。

字段语义、七要素/证据/对齐规则见 `references/output-contract.md`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。

- **阶段 0（0–3 分钟，≤6 请求）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式、周期/经费/数据约束、`output_contract` 字段。运行骨架落盘：
  `python scripts/proposal.py init --case /input/case.json --result /app/output/result.json`
  输入：task.md、case.json → 输出：`/app/output/result.json`（status=partial 的诚实骨架，含契约全部空字段 + 信封字段）。
- **阶段 1（3–8 分钟，≤16 请求）**：读取全部材料（PDF 解析文本/OCR），建立 `sources` 清单（path/kind）与逐字 `quote`；回填 `title`、`summary`、`constraints`、`background_gap`（已知/未知/待验证三段）及初版 `evidence_map`。
- **阶段 2（8–13 分钟，≤24 请求）**：完成七要素——研究问题（1–3 个可证伪，带指标/样本/对照/时间点）、目标（每条指向问题 + 考核标准）、技术路线（步骤带输入/输出/依赖/服务目标）、阶段计划（里程碑带起止月）、风险（四类：数据获取/方法可行性/进度/伦理与合规，每条带触发信号/影响/备选/决策时点）、预期成果（带考核方式）；写 `evidence_map`（每条带 locator + 逐字 quote）与 `uncertainty`/`pending_human`。
- **阶段 3（13–15 分钟，≤10 请求）**：运行 `python scripts/proposal.py check --case /input/case.json --result /app/output/result.json` 修复 FAIL；运行 `python scripts/proposal.py render --result /app/output/result.json --out /app/output` 生成产物；对照 `references/checklist.md` 自检；确定 `status`；重跑 `check` 确认 FAIL=0。

脚本调用统一使用相对路径（`scripts/proposal.py ...`），在 SKILL.md 所在目录（Skill 根目录）执行；若当前目录不是 Skill 根目录，先定位 SKILL.md 再执行。脚本仅依赖 Python 标准库，可重复运行、幂等、无随机性。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`，可运行 `python scripts/selftest.py`）：

- [ ] result.json 合法、含 case_id 与 output_contract 全部字段 + 信封字段（status/sources/uncertainty/pending_human/change_log）。
- [ ] 七要素齐全：背景缺口、研究问题、目标、技术路线、阶段计划、风险（四类）、预期成果（带考核方式）；边界已写明。
- [ ] 研究问题具体、可证伪，且 1–3 个；背景是“已知—未知—待验证”缺口而非文献罗列。
- [ ] 要素对齐：目标→问题、路线→目标/问题、成果→目标、验证→问题，ref 均能回查（`check` 捕获 FAIL）。
- [ ] 计划可行：里程碑起止月不超给定周期，任务量与经费/数据/人员相容（`check` 捕获 FAIL）。
- [ ] 每个 `fact`/`summary` 论断与关系的 `evidence` 均能在 `evidence_map` 找到，且带 `locator` + 逐字 `quote`；不新增材料外事实。
- [ ] 风险均有触发信号 + 影响范围 + 备选方案 + 决策时点；材料冲突标 `kind=conflict`，推断标 `kind=inference` 并列入 `uncertainty`。
- [ ] `check` 通过（FAIL=0）；`render` 已生成 proposal.md/evidence_map.md/schedule.mmd/preview.svg；artifacts 非空且文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录从骨架到成品的增量过程。

# 降级策略

- **材料缺失/无法解析**：不补造内容；受影响要素标 `kind: inference` 或移除，写入 `uncertainty`（kind=missing）与 `pending_human`，`status` 降级为 `partial` 或 `insufficient_evidence`，并在 `summary` 后说明 `limitations`。
- **信息自相矛盾**：并列两个版本并标 `kind=conflict`、指明各自出处，写入 `pending_human` 请人工裁决，不擅自选一个。
- **格式/软件不支持**：优先降级为 Markdown + Mermaid + SVG（纯标准库可产出）；需要 Office 格式而 office-renderer 不可用时，交付 Markdown 并注明“目标格式未生成，需运行时渲染工具”。
- **周期过短/指标不可达**：不悄悄改指标或压缩周期；如实写入 `uncertainty`（kind=infeasible）与 `pending_human`，建议人工调整边界。
- **节点/要素规模爆炸**：只保留与“问题↔目标↔验证”直接相关的要素，其余并入 `summary` 或 `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed`/`partial`，在 `change_log` 与 `limitations` 说明原因；**绝不编造评测分数、软件状态或科学数据**。
- **budget 用尽**：以最近一次 `check` 通过（或最接近通过）的 partial 结果为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

输入 case.json 片段：

```json
{"case_id": "demo", "output_contract": {"title": {"required": true}, "summary": {"required": true}, "seven_elements": {"required": true}, "evidence_map": {"required": true}, "artifacts": {"required": true}}}
```

执行：`python scripts/proposal.py init --case /input/case.json --result /app/output/result.json && python scripts/proposal.py render --result /app/output/result.json --out /app/output && python scripts/proposal.py check --case /input/case.json --result /app/output/result.json`

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：开题方案骨架",
  "summary": "示意内容，非正式案例结果",
  "seven_elements": {"background_gap": {"text": null, "known": [], "unknown": [], "to_verify": [], "evidence": []}, "research_questions": [], "objectives": [], "technical_route": [], "schedule": [], "risks": [], "expected_outcomes": []},
  "evidence_map": [],
  "artifacts": [],
  "sources": [],
  "uncertainty": [{"item": "result", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00", "action": "init skeleton", "reason": "示例"}]
}
```
