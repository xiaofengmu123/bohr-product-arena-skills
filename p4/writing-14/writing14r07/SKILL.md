---
name: weekly-skill
description: 开题报告与研究计划书（research proposal）学术写作 Skill。从 /input/case.json 与 task.md 指定的科研材料中抽取约束与事实，按“研究背景与缺口、研究问题、目标与考核、技术路线、阶段计划、风险与备选、预期成果”七要素生成可复核、可编辑的开题方案，逐条保留论断→材料（locator+逐字 quote）溯源，产出 /app/output/result.json 与可编辑 Markdown/Mermaid/SVG 预览。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是“开题报告与研究计划书”任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**可复核、可编辑、证据可追溯**的开题方案或结构化写作产物时，按本 Skill 执行。

核心目标对应评分重点：**立得住**（研究问题具体、可证伪，背景真的指向缺口而非综述）、**对得上**（目标/路线/验证/成果与问题一一对应）、**排得开**（阶段计划落在给定周期、经费、数据、人员条件内，里程碑与任务量匹配）、**说得清**（风险有触发信号与备选方案，论断与引用一一对应、可逐条回溯）。本版侧重点：**鲁棒性**——空输入、坏 JSON、缺参数、超大文件都要优雅降级、绝不崩溃。

## 何时不使用

纯问答、无材料输入、或题目明确要求其他格式且与开题无关时，按 task.md 直接处理；本 Skill 的七要素/溯源/自检规范不强制套用。

# 输入契约

- 任务说明 `/input/task.md`（必读，先于任何假设）；案例输入 `/input/case.json`（可能含 `case_id`、`output_contract`（或 `output_schema`/`required_fields`/`contract`）、材料路径、体裁/受众/周期/经费/数据与格式约束）。
- 科研材料按 case.json 与 task.md 指定路径读取**真实文件**。文本按 `utf-8-sig` 容错；PDF 用解析文本/OCR 读取，不假设标签内容；解析层缺失时用运行时 PDF/OCR 工具。单文件 >2MB 跳过并记录。
- 运行目录：中间产物写 `/app/work/`；最终产物与可编辑产物写 `/app/output/`。资源入口 `environment/resources.yaml`（若存在）声明预装工具，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，含：

1. `case_id`：与 case.json 一致；拿不到填 `"unknown"` 并在 uncertainty 说明。
2. **题目 `output_contract` 的全部字段**：原样回填（缺值给 `null`/`[]`，不改名、不删未知键）；契约未声明时用默认字段 `title`、`summary`、`proposal`、`artifacts`。
3. 信封字段：`status`（complete/partial/insufficient_evidence/input_error/failed）、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。
4. 主产物 `proposal`：七要素结构（研究背景与缺口、研究问题、目标与考核、技术路线、阶段计划、风险与备选、预期成果、边界），字段语义与风险词表见 `references/output-contract.md`。
5. 可编辑产物：`scripts/main.py render` 生成 `proposal.md`（主稿）、`evidence_map.json`（证据映射）、`route.mmd` 与 `route.svg`（技术路线预览）；题目要求 Office 格式时，用运行时 `office-renderer` 生成并附真实渲染的 PNG/PDF 预览。路径写入 `artifacts`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内先落盘 partial 结果骨架，之后只做增量改进**；宁可交付诚实 partial，不可最后无产出。所有脚本调用用相对路径 `scripts/main.py ...`，在 SKILL.md 所在目录（Skill 根目录）执行；先定位 SKILL.md 所在目录再执行。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/周期/经费/格式约束、`output_contract` 字段。落盘骨架：`python scripts/main.py init --case /input/case.json --result /app/output/result.json`（case.json 缺失/坏 JSON 时仍落盘 input_error 骨架，退出码 0）。
- **步骤 1（1–3 分钟）**：扫描材料并建立 sources 清单：`python scripts/main.py load --input /input --sources-out /app/work/sources.json`；对每份材料补 `locator`（页码/段落）+ 逐字 `quote`。回填 `title`、`summary`、初版 `sources`，用 `merge` 增量写入。输入：材料文件 → 输出：sources 清单 + 回填骨架。
- **步骤 2（3–6 分钟）**：抽取七要素并组织——背景与缺口（已知—未知—待验证）、1–3 个可证伪问题、目标与考核（指标/样本/对照/时间点）、技术路线（步骤/输入/输出/依赖/可并行）、阶段计划（周期/经费/人员/里程碑/关键路径）、风险（四类：数据获取、方法可行性、进度、伦理合规，各配触发信号/影响/备选/决策点）、预期成果与考核。每条关键论断挂 `sources`；标 `kind`（fact=原文可指认，summary=多段归纳，inference=Agent 补出）。无据连接不写。输入：sources + 材料 → 输出：`proposal` 七要素初版。
- **步骤 3（6–11 分钟）**：`python scripts/main.py check` 修复 FAIL；`python scripts/main.py render` 生成可编辑产物与预览；如需 docx/pptx，用运行时工具生成并渲染 PNG/PDF 预览。输入：result.json → 输出：artifacts + 预览。
- **步骤 4（11–14 分钟）**：对照 `references/checklist.md` 逐条自检；把材料缺口、冲突、推断写入 `uncertainty`，需人工裁决写入 `pending_human`，动作写入 `change_log`；确定 `status`；重跑 `check` 确认 FAIL=0。输入：checklist + result.json → 输出：最终 `result.json`。

脚本仅依赖 Python 标准库，可重复运行、幂等（除时间戳外结果一致）。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全；事实、数据、引用忠于材料，无凭空术语、无张冠李戴。
- [ ] 指标阈值有依据；目标/路线/验证/成果与问题一一对应（`problem_ref` 无悬空）。
- [ ] 阶段计划落在给定周期/经费/人员内，里程碑与任务量匹配，关键路径标注。
- [ ] 风险四类各检一次，每条有触发信号/影响/备选/决策点。
- [ ] 每个论断的 sources 都能在清单找到且带 locator 或逐字 quote；fact/summary/inference 区分正确。
- [ ] `python scripts/main.py check` 通过（FAIL=0）；`render` 已生成 proposal.md/evidence_map.json/route.mmd/route.svg；artifacts 非空且文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录从骨架到成品的增量过程。
- [ ] 材料缺口/冲突/推断已在 uncertainty/pending_human 说明；未编造分数、软件状态或科学数据。
- [ ] `python scripts/selftest.py` 通过（空输入、坏 JSON、缺参数、超大文件均不崩溃）。

# 降级策略

- **空输入/坏 JSON**：退出码 0，写 `status=input_error`/`insufficient_evidence` 合法结果，列出缺口；不抛异常、不补造内容。
- **材料缺失/无法解析**：受影响要素标 `kind: inference` 或从 proposal 移除，写入 `uncertainty`（kind=missing），`status` 降为 `partial`，在 `summary` 后说明 `limitations`。
- **信息自相矛盾**：并列呈现两个版本并标 `kind=conflict`，指明各自出处，写入 `pending_human` 建议人工裁决，不擅自选一个。
- **超大文件（>2MB）/文件过多**：跳过并记录 `oversized` 到 `degradations`，不因单文件撑爆内存。
- **缺软件/格式不支持**：优先降级为 Markdown + Mermaid + SVG（纯标准库可产出）；需要 Office 格式而运行时工具不可用时，交付 Markdown 并注明“目标格式未生成，需运行时渲染工具”。
- **权限/写盘失败**：降级到最小合法产物，`status` 如实降级为 `partial`/`failed`，在 `limitations` 与 `change_log` 说明；**绝不编造评测分数或伪造运行结果**。
- **预算用尽**：以最近一次 `check` 通过的 partial 结果为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

输入 case.json 片段：

```json
{
  "case_id": "demo",
  "output_contract": {
    "title": {"required": true},
    "summary": {"required": true},
    "proposal": {"required": true}
  }
}
```

执行：

```bash
python scripts/main.py init --case /input/case.json --result /app/output/result.json
python scripts/main.py load --input /input --sources-out /app/work/sources.json
# ……Agent 回填 proposal 各要素与 sources（用 merge 增量写入）……
python scripts/main.py check --result /app/output/result.json
python scripts/main.py render --result /app/output/result.json --output /app/output
```

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：某方向开题报告",
  "summary": "示意内容，非正式案例结果",
  "proposal": {
    "research_background": {"known": [], "unknown": [], "gap": null, "gap_sources": []},
    "research_problems": [],
    "objectives": [],
    "technical_route": [],
    "phase_plan": {"total_period": null, "milestones": []},
    "risks": [],
    "expected_outcomes": []
  },
  "artifacts": ["proposal.md", "evidence_map.json", "route.mmd", "route.svg"],
  "sources": [],
  "uncertainty": [{"item": "materials", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}]
}
```
