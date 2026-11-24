---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 /input/task.md 指定的课题指南与科研材料中抽取周期/经费/数据/人员/受众/格式约束，生成七要素齐全（背景缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果）且通过四问自检（立得住/对得上/排得开/说得清）的开题方案，逐条挂证据映射（locator+逐字 quote），产出可编辑 Markdown 与真实 docx，并写 /app/output/result.json（含 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是"开题报告与研究计划书"任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据课题指南与科研材料产出一份**可复核、可编辑、证据可追溯**的开题方案时，按本 Skill 执行。

核心目标对应评分四个重点：**立得住**（研究问题具体可证伪，背景真正指向缺口而非综述一遍）、**对得上**（目标/技术路线/验证方式/预期成果与问题一一对应）、**排得开**（阶段计划落在给定周期/经费/数据/人员内）、**说得清**（风险有触发信号与备选方案，论断与引用可回溯可复核）。

**何时不使用**：纯问答、无材料输入、或题目明确要求其他体裁且与开题方案无关时，按 task.md 直接处理，不强套七要素/四问/证据链规范。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，可能含 `case_id`、`output_contract`（或 `output_schema`/`required_fields`/`contract`/`schema`/`fields` 等同义键）、材料路径、体裁/受众/格式约束。
- 科研材料：按 case.json 与 task.md 指定路径读取**真实文件**；PDF 用解析文本读取，不假设标签内容，解析层缺失时用运行时 PDF 解析/OCR 工具，扫描件用 OCR。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在），声明的预装工具以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 的全部字段**：优先从 case.json 提取字段名并逐一原样回填，缺值给 `null`/`[]`，不改名、不删未知键。
3. 标准内容字段：`title`、`summary`、`constraints`、`seven_elements`、`four_checks`、`evidence_map`、`artifacts`（字段语义与词表见 `references/output-contract.md`）。
4. 信封字段：`status`（`complete`/`partial`/`failed`/`insufficient_evidence`/`input_error`）、`sources`、`uncertainty`、`pending_human`、`change_log`。
5. 可编辑产物：`scripts/main.py build` 生成 `proposal.md`（七要素、章节编号）、`evidence_map.md`、`checks.md`、`proposal.docx`（标准库生成的真实 OOXML）。题目要求 docx/pptx 时，用运行时 `office-renderer` 渲染真实 PDF/PNG 预览；工具不可用时如实降级并在 `uncertainty` 说明。所有产物路径写入 `artifacts`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版合法 partial 骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式、`output_contract` 字段。运行：
  `python scripts/main.py init --case /input/case.json --out /app/output/result.json`
  输入 task.md、case.json → 输出含全部契约键的 partial 骨架。
- **步骤 1（1–4 分钟）**：读取全部材料（PDF 解析文本/OCR），按材料段落建立 `sources`（locator 页码/段落 + 短 quote）；抽取 `constraints`（周期/经费/数据/人员/受众/格式）；回填 `title`/`summary`/`constraints`/`sources`。输入：材料文件 → 输出：sources + constraints。
- **步骤 2（4–9 分钟）**：写七要素——背景缺口（known/unknown/to_verify）、1–3 个可证伪问题、每个问题的目标（指标/样本/对照/时间点）、技术路线（步骤/输入/输出/依赖/并行）、阶段计划（里程碑/关键路径/资源）、风险（四类：data_acquisition/method_feasibility/schedule/ethics_compliance，各带触发信号/影响/备选/决策时点）、预期成果（考核方式+关联目标）。逐条建 `evidence_map`（fact/summary/inference），填 `four_checks`。输入：sources+constraints+材料 → 输出：seven_elements、evidence_map、four_checks。
- **步骤 3（9–12 分钟）**：`python scripts/main.py check --result /app/output/result.json --case /input/case.json --out /app/output` 修复 FAIL；`python scripts/main.py build ...` 生成可编辑产物与 docx；需要 Office 预览时用 `office-renderer` 渲染并写 `artifacts`。输入：result.json → 输出：artifacts + 预览。
- **步骤 4（12–14 分钟）**：对照 `references/checklist.md` 逐条自检；把材料缺口、冲突、推断写入 `uncertainty`，需人工裁决的写入 `pending_human`，动作写入 `change_log`；确定 `status`；重跑 `check` 确认 FAIL=0；`python scripts/main.py selftest` 确认脚本健康。

**脚本调用统一使用相对路径**（`scripts/main.py ...`），在 SKILL.md 所在目录（Skill 根目录）执行；脚本仅依赖 Python 标准库，幂等可重复运行。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全、问题可证伪、目标/路线/成果与问题一一对应、计划与资源相容。
- [ ] 每条风险有触发信号与备选；每个关键数字/引用在 evidence_map 有 locator+quote。
- [ ] fact/summary/inference 区分正确，推断已列 uncertainty；无材料外事实。
- [ ] `check` 通过（FAIL=0）；`build` 已生成 proposal.md/.docx 等；artifacts 非空且真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录增量过程。

# 降级策略

- **材料缺失/无法解析**：不补造内容；受影响要素标 `kind: missing` 并写入 `uncertainty`，`status` 降为 `partial`，在 `summary` 后写 `limitations`。
- **信息自相矛盾**：并列呈现两个版本并标 `kind: conflict`，各指出来源，建议人工裁决，不擅自选一边。
- **格式/软件不支持**：优先 Markdown 大纲 + 标准库 docx；需 Office 格式而 `office-renderer` 不可用时，交付 Markdown+docx 并注明"预览未生成，需运行时渲染工具"。
- **周期过短/指标不可达/学科规范冲突**：不硬凑，写入 `uncertainty`（kind=limitation）并给可考核的替代口径，标记 `pending_human`。
- **节点/要素规模爆炸**：只保留对研究问题/结论/任务直接相关的项，其余并入 `note` 或 `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed` 或 `partial`，在 `limitations` 与 `change_log` 说明原因；**绝不编造评测分数、软件状态或科学数据**。
- **budget 用尽**：以最近一次 `check` 通过的 partial 为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

输入 case.json 片段：

```json
{"case_id": "demo", "output_contract": {"title": {"required": true}, "proposal": {"required": true}, "evidence_map": {"required": true}}}
```

执行：

```bash
python scripts/main.py init  --case /input/case.json --out /app/output/result.json
python scripts/main.py check --result /app/output/result.json --case /input/case.json --out /app/output
python scripts/main.py build --result /app/output/result.json --out /app/output
python scripts/main.py selftest
```

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：某课题开题方案",
  "summary": "示意内容，非正式案例结果",
  "proposal": null,
  "evidence_map": [],
  "seven_elements": {},
  "artifacts": ["proposal.md", "proposal.docx"],
  "sources": [],
  "uncertainty": [{"item": "result", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}]
}
```
