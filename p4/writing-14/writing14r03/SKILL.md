---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 task.md 指定的科研材料（课题指南、前期数据、文献、组内讨论等）中抽取事实、周期、经费、数据与人员约束，产出“研究背景—问题—目标—技术路线—阶段计划—风险—预期成果”七要素齐全、论断与引用一一对应、可继续编辑的开题方案；写 /app/output/result.json（含 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）与 proposal.md、evidence.md，并内置 init/check/render 脚本与确定性自检。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是“开题报告与研究计划书”写作任务的执行者。核心不是文献综述，而是围绕四件事完成可复核的写作产物：**立得住**（研究问题具体、可证伪，背景材料真的指向缺口）、**对得上**（目标、技术路线、验证方式与预期成果和问题一一对应）、**排得开**（阶段计划落在给定周期、经费、数据与人员条件内，里程碑与任务量匹配）、**说得清**（风险有触发信号与备选方案，关键论断与引用可追溯）。

当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出开题报告或研究计划书时，按本 Skill 执行。

## 何时不使用

纯问答、无材料输入，或 task.md 明确要求其他体裁（论文/综述/幻灯片等）且与开题无关时，按 task.md 直接处理；本 Skill 的七要素、证据与自检规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，含 `case_id`、`output_contract`（或 `output_schema`/`required_fields`/`contract` 等同义键）、材料路径、体裁/受众/格式约束，以及周期、经费、数据、人员等开题约束。
- 科研材料：按 case.json 与 task.md 指定路径读取真实文件。**PDF 用解析文本读取，不假设标签内容**；解析层缺失时用运行时 PDF 解析/OCR 工具，扫描件用 OCR。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在）；声明预装工具以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 的全部字段**：从 case.json 提取字段名并原样回填（缺值给 `null`/`[]`，不改名、不删未知键）；契约未声明时用默认字段 `title`、`summary`、`proposal`、`artifacts`。
3. **七要素**：`background`、`problems`、`objectives`、`methods`、`plan`、`risks`、`expected_outcomes`，另有 `references`（引用映射）、`boundaries`（边界）。
4. **信封字段**：`status`（complete/partial/failed/insufficient_evidence/input_error）、`sources`（证据清单）、`uncertainty`（不确定/待确认）、`pending_human`（需人工确认）、`change_log`（修改记录），以及 `errors`/`degradations`/`limitations`。
5. **可编辑产物**：`python scripts/main.py render` 生成 `proposal.md`（可编辑 Markdown 方案）与 `evidence.md`（证据溯源表）；题目要求 docx/pptx 时，用运行时 `office-renderer` 生成并附真实渲染的 PNG/PDF 预览。所有产物路径写入 `artifacts`。

字段语义、七要素子字段、source/风险词表、确定性规则见 `references/output-contract.md`；完整字段与词表以该文件为准。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版 `status=partial` 结果骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式约束、周期/经费/数据/人员条件、`output_contract` 字段。运行骨架落盘：
  `python scripts/main.py init --case /input/case.json --result /app/output/result.json`
  输入：task.md、case.json → 输出：`/app/output/result.json`（status=partial 的诚实骨架）。
- **步骤 1（1–3 分钟）**：读取全部材料（PDF 解析文本/OCR），建立 `sources` 清单（`locator`=文件#页码/段落 + 逐字 `quote` + `claim` + `kind`），抽取事实、数字、引用与约束。回填 `title`、`summary`、`limitations` 与初版 `sources`，跑一次 `check` 确认契约字段齐。
- **步骤 2（3–6 分钟）**：按“已知→未知→待验证”写 `background`；收敛 1–3 个可证伪 `problems`；为每个问题写 `objectives`（`problem_ref` + `criteria`：指标/样本/对照/时间点）；写 `methods`（step/input/output/depends_on）。跑 `check` 修复 FAIL。
- **步骤 3（6–11 分钟）**：写 `plan`（period/budget/milestones/关键路径/可并行）、`risks`（分类：数据获取/方法可行性/进度/伦理与合规/资源；每条 trigger/impact/alternative/decision_point）、`expected_outcomes`（outcome+verification）、`references`（claim↔source_id 一一对应）、`boundaries`。跑 `render` 生成产物；docx 需求用运行时工具渲染预览。
- **步骤 4（11–14 分钟）**：对照 `references/checklist.md` 逐条自检；材料缺口/冲突/推断写入 `uncertainty`；需人工裁决项写入 `pending_human`；动作写入 `change_log`；确定 `status`；重跑 `check` 确认 FAIL=0。

**脚本调用统一使用相对路径**（`scripts/main.py ...`），在 SKILL.md 所在目录（Skill 根目录）执行；若当前目录不是 Skill 根目录，先定位 SKILL.md 所在目录再执行。脚本仅依赖 Python 标准库，可重复运行、幂等、确定性渲染。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] result.json 合法，含 case_id、output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全且非空；`background` 有具体 `gap`；`problems` 1–3 个且每条有 question/falsifiable/gap_link。
- [ ] 对得上：objectives 有 problem_ref 且指向存在的 problem；criteria 含指标/样本/对照/时间点；methods 有 step/input/output/depends_on。
- [ ] 排得开：plan 有 period/budget/milestones，里程碑落在给定周期与资源内，关键路径与可并行已标。
- [ ] 说得清：risks 每条含 category/trigger/impact/alternative/decision_point；references 每条 claim↔source_id 一一对应。
- [ ] 每个 source 有 id + locator + 逐字 quote；fact/summary/inference 区分正确，inference 已列入 uncertainty。
- [ ] `python scripts/main.py check` FAIL=0（WARN 已说明）；`render` 已生成 proposal.md/evidence.md；artifacts 文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录从骨架到成品的增量过程。
- [ ] 同输入两次 render 产物一致（确定性）；未编造分数、软件状态或科学数据。

# 降级策略

- **材料缺失/无法解析**：不补造内容；受影响要素标 `kind: missing` 或移除，写入 `uncertainty`，`status` 降为 `partial`，并在 `summary` 后说明 `limitations`。
- **信息自相矛盾**：并列呈现两个版本并标 `kind=source_conflict`，各指来源，写入 `pending_human` 建议人工裁决，不擅自选一个。
- **周期/经费/指标不可达**：在 `plan`/`risks` 如实标注不可达与触发信号，不夸大里程碑，列入 `uncertainty`。
- **格式/软件不支持**：优先降级为 Markdown（proposal.md/evidence.md，纯标准库可产出）；需要 docx 而运行时工具不可用时，交付 Markdown 并注明“目标格式未生成，需运行时渲染工具”。
- **节点/要素爆炸**：只保留与问题/目标/路线直接相关的约束，其余并入 `boundaries.out_of_scope` 或写入 `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed` 或 `partial`，在 `limitations` 与 `change_log` 说明原因；**绝不编造评测分数或伪造运行结果**。
- **budget 用尽**：以最近一次 `check` 通过的 partial 结果为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

输入 case.json 片段：

```json
{
  "case_id": "demo",
  "output_contract": {
    "title": {"required": true},
    "summary": {"required": true},
    "proposal": {"required": true},
    "artifacts": {"required": true}
  }
}
```

执行：`python scripts/main.py init && python scripts/main.py check && python scripts/main.py render`

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：某课题开题方案",
  "summary": "示意内容，非正式案例结果",
  "background": {}, "problems": [], "objectives": [], "methods": [],
  "plan": {}, "risks": [], "expected_outcomes": [], "references": [],
  "artifacts": [], "sources": [], "uncertainty": [], "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}]
}
```

自检：`python scripts/selftest.py`（验证仅标准库、契约往返、确定性渲染；退出码 0 为通过）。
