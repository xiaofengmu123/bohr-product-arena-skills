---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 task.md 指定的科研材料中抽取事实底稿、受众、文体、格式与资源约束，生成研究背景、问题定义、目标、技术路线、阶段计划、风险、预期成果七要素齐全、与给定周期/经费/数据/人员相容、证据可逐条回溯的开题方案，产出 /app/output/result.json（含 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）及可编辑 markdown、证据映射与预览。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是「开题报告与研究计划书」任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**可复核、可编辑、证据可逐条回溯**的开题方案或研究计划时，按本 Skill 执行。

核心目标（对应题目四个问题）：**立得住**——研究问题具体、可证伪，背景指向明确缺口而非综述；**对得上**——目标、技术路线、验证方式、预期成果与问题一一对应；**排得开**——阶段计划落在给定周期、经费、数据与人员条件内，里程碑与任务量匹配；**说得清**——风险有触发信号与备选方案，关键论断与引用可追溯。

## 何时不使用

纯问答、无材料输入、或题目要求与开题方案无关的其他格式时，按 task.md 直接处理；本 Skill 的七要素、证据映射与可行性校验规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，可能含 `case_id`/`id`、`output_contract`（或 `output_schema`/`required_fields`/`contract` 等同义键）、材料路径、体裁/受众/格式约束、周期/经费/数据/人员条件。
- 科研材料：按 case.json 与 task.md 指定路径读取真实文件。**PDF 用解析文本读取，不假设标签内容**；解析层缺失时用运行时 PDF 解析/OCR 工具，扫描件用 OCR 工具。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在），声明的预装工具以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 的全部字段**：优先从 case.json 提取字段名并逐一填写；契约未声明时使用默认字段 `title`、`summary`、`deliverable`、`seven_elements`、`evidence_map`、`artifacts`（详见 `references/output-contract.md`）。
3. 信封字段：`status`（`complete`/`partial`/`failed`）、`sources`（证据源清单）、`uncertainty`（不确定项）、`pending_human`（待人工确认项）、`change_log`（修改记录）、`runtime`、`errors`、`degradations`、`limitations`。
4. 可编辑产物：`scripts/main.py render` 生成 `proposal.md`（七要素分节、保留编号与证据标注）与 `evidence_map.json`；题目要求 docx/pptx/xmind 时，用运行时 `office-renderer`/`browser-automation` 生成并附真实渲染的 PNG/PDF 预览。所有产物路径写入 `artifacts`。

**证据溯源（本版重点）**：七要素中每条关键论断带 `claim_id`、`locator`（页码/段落定位）与逐字 `quote`；`evidence_map` 为扁平索引，字段更细（`claim_id`/`section`/`locator`/`quote`/`source_id`/`kind`/`verdict`），确保「论断—引用」一一对应、评分人可复跑。完整字段与词表以 `references/output-contract.md` 为准。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版结果骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式约束、周期/经费/数据/人员条件、`output_contract` 字段。落盘骨架：
  `python scripts/main.py init --case /input/case.json --out /app/output/result.json --work /app/work`
  输入：task.md、case.json → 输出：`/app/output/result.json`（status=partial 的诚实骨架）。
- **步骤 1（1–3 分钟）**：读取全部材料（PDF 解析文本/OCR），按材料段落建立 `sources` 清单（`source_id` + `locator` + 逐字 `quote`）。回填 `title`、`summary` 与初版 `sources`，运行一次 `check` 确认契约字段齐。
- **步骤 2（3–8 分钟）**：构建 `seven_elements`，每条论断标 `claim_id` 并挂 `locator`+`quote`+`kind`（fact=原文可指认、summary=多段归纳、inference=Agent 补出）；按「对得上」校验目标/路线/验证/成果与问题的对应，按「排得开」校验阶段计划与周期/经费/人员相容，触发的缺口写入 uncertainty/pending_human。
- **步骤 3（8–12 分钟）**：运行 `check` 修复 FAIL；运行 `render` 生成可编辑产物与预览；题目要求 Office 格式时，用运行时工具生成并渲染 PNG/PDF 预览。
- **步骤 4（12–14 分钟）**：对照 `references/checklist.md` 逐条自检；把材料缺口、冲突、推断写入 `uncertainty`，待人工裁决项写入 `pending_human`，动作写入 `change_log`；确定 `status`（complete/partial/failed）；重跑 `check` 确认 FAIL=0。

**脚本调用统一使用相对路径**（`scripts/main.py ...`），在 SKILL.md 所在目录（Skill 根目录）执行；若当前工作目录不是 Skill 根目录，先定位 SKILL.md 所在目录再执行。脚本仅依赖 Python 标准库，可重复运行、幂等。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] result.json 合法，含 case_id、output_contract 全部字段 + 信封字段（status/sources/uncertainty/pending_human/change_log）。
- [ ] 七要素齐全且非空：研究背景、问题定义、目标、技术路线、阶段计划、风险、预期成果；边界写明。
- [ ] 每条 `fact`/`summary` 论断带 `locator` + 逐字 `quote`，fact/summary/inference 区分正确；无材料外新增事实。
- [ ] 目标/路线/验证/成果与问题一一对应（对得上）；阶段计划落在给定周期/经费/数据/人员内（排得开）。
- [ ] 风险按数据获取/方法可行性/进度/伦理与合规分类，每条有触发信号、影响范围、备选方案与决策时点。
- [ ] `check` 通过（FAIL=0）；`render` 已生成 proposal.md / evidence_map.json；artifacts 非空且文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录从骨架到成品的增量过程。
- [ ] 材料缺口/冲突/推断已写入 uncertainty，待人工确认项写入 pending_human；未编造分数、软件状态或科学数据。

# 降级策略

- **材料缺失/无法解析**：不补造内容；受影响论断标 `kind: inference` 或移除，写入 `uncertainty`（kind=missing），`status` 降为 `partial`，并在 `summary` 后说明 `limitations`。
- **信息自相矛盾**：并列两个版本并标 `verdict: conflict`，各带出处 quote，写入 `pending_human` 建议人工裁决，不擅自选一个。
- **周期过短/指标不可达/预算或人员不足**：可行性校验标 WARN，写入 `pending_human` 并给出调整后的备选里程碑，不擅自改动给定条件。
- **格式/软件不支持**：优先降级为 Markdown 稿 + 证据映射（纯标准库可产出）；需要 Office 格式而运行时工具不可用时，交付 Markdown 并注明「目标格式未生成，需运行时渲染工具」。
- **要素/证据规模爆炸**：只保留与问题/结论/任务直接相关的论断，其余并入 `note` 或写入 `uncertainty`（out_of_scope）。
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
    "seven_elements": {"required": true},
    "evidence_map": {"required": true},
    "artifacts": {"required": true}
  }
}
```

执行：

```bash
python scripts/main.py init   --case /input/case.json --out /app/output/result.json --work /app/work
python scripts/main.py check  --result /app/output/result.json --case /input/case.json
python scripts/main.py render --result /app/output/result.json --out /app/output --work /app/work
python scripts/selftest.py
```

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：开题方案骨架",
  "summary": "示意内容，非正式案例结果",
  "seven_elements": {"background": [], "problem_definition": [], "objectives": [],
    "technical_route": [], "stage_plan": [], "risks": [], "expected_outcomes": []},
  "evidence_map": [],
  "artifacts": {},
  "sources": [],
  "uncertainty": [{"item": "seven_elements", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init skeleton", "reason": "示例"}]
}
```
