---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 task.md 指定的科研材料中抽取约束与已知结论，产出七要素齐全（研究背景/问题/目标/技术路线/阶段计划/风险/预期成果）、与给定周期经费数据人员相容、论断可逐条回溯的可编辑开题方案，写入 /app/output/result.json（含 case_id 与题目 output_contract 全部字段，再加 status/sources/uncertainty/pending_human/change_log），并生成可编辑 Markdown、Mermaid 路线图与 SVG 阶段预览。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是"开题报告与研究计划书"写作执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**可编辑、可复核、来源可回溯**的开题方案时，按本 Skill 执行。

核心目标（对应题面四个问题）：
- **立得住**：研究问题具体、可证伪，背景材料真指向该缺口，而非把相关领域综述一遍。
- **对得上**：目标、技术路线、验证方式、预期成果与问题一一对应，而不是各写各的。
- **排得开**：阶段计划落在给定周期、经费、数据与人员条件内，里程碑与任务量匹配。
- **说得清**：风险有触发信号与备选方案，关键论断与引用一一对应、可追溯、可复核。

## 何时不使用

无材料输入、纯问答、或题目要求与本任务无关的格式时，按 task.md 直接处理；本 Skill 的七要素/证据/可行性规范不强制套用。

# 输入契约

- 任务说明 `/input/task.md`：必读，先于任何假设；提取体裁、受众、格式与周期/经费/数据/人员约束。
- 案例输入 `/input/case.json`：可能含 `case_id`/`id`、`output_contract`（或 `output_schema`/`required_fields`/`contract` 等同义键）、材料路径、约束。
- 科研材料：按 case.json 与 task.md 指定路径读真实文件；**PDF 用解析文本读取，解析层缺失时用运行时 PDF/OCR 工具**，不依据标签补造内容。
- 运行目录：中间产物放 `/app/work/`；最终与可编辑产物放 `/app/output/`。
- 资源入口 `environment/resources.yaml`（若存在）：声明的预装工具以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 output_contract 全部字段**：从 case.json 提取字段名逐一回填（未知键保留、不改名、不删；缺失时给 `null`/`[]`）；契约未声明时用默认字段 `title`、`summary`、`proposal`、`evidence_map`、`artifacts`。
3. `proposal` 七要素对象（字段语义与词表见 references/output-contract.md）：`background`（known/unknown/gap）、`research_questions`、`objectives`、`technical_route`、`phase_plan`、`risks`、`expected_outcomes`。
4. 信封字段：`status`（complete/partial/failed）、`sources`、`uncertainty`、`pending_human`、`change_log`；另附 `errors`、`degradations`、`constraints`。
5. 可编辑产物：`python scripts/main.py render` 生成 `proposal.md`（可编辑正文，保留章节编号）、`proposal_plan.mmd`（技术路线依赖图）、`proposal_plan.svg`（阶段计划预览）；题目要求 docx/pptx 时用运行时 `office-renderer` 生成并附真实渲染 PDF/PNG 预览。路径写入 `artifacts`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟必须先落盘一版 partial 结果骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式、周期/经费/数据/人员约束、`output_contract` 字段。落盘骨架：
  `python scripts/main.py init --case /input/case.json --out /app/output/result.json`
  输入：task.md、case.json → 输出：`/app/output/result.json`（status=partial 的诚实骨架）。
- **步骤 1（1–3 分钟）**：读全部材料，按段落建立 `sources` 清单（locator=页码/段落 + 逐字 quote）。回填 `title`、`summary`、`constraints`（period_months/budget/personnel/data_scope）与初版 `sources`。
- **步骤 2（3–7 分钟）**：抽七要素——背景（已知/未知/缺口）、问题（1–3 个，各写可证伪判据）、目标（指标/样本/对照/时间点，`answer_to` 指向 Q1–Q3）、技术路线（step_id/input/output/depends_on）、阶段计划（phase/milestone/period/resources）、风险（category/trigger/impact/mitigation/decision_point）、预期成果（outcome/assessment）。每条论断挂 `sources`（locator+quote），fact/summary/inference 区分正确。
- **步骤 3（7–10 分钟）**：`python scripts/main.py check --case /input/case.json --out /app/output/result.json` 修复 FAIL、说明 WARN；`python scripts/main.py render --out /app/output/result.json` 生成产物与预览；需 Office 时用 office-renderer 生成并渲染 PNG/PDF。
- **步骤 4（10–14 分钟）**：对照 references/checklist.md 逐条自检；把缺口/冲突/推断写入 `uncertainty`，待人工确认写入 `pending_human`，动作写入 `change_log`；确定 `status`；重跑 `check` 确认 FAIL=0。

脚本统一用相对路径（`scripts/main.py ...`），在 SKILL.md 所在目录（Skill 根目录）执行；仅依赖 Python 标准库，可重复、幂等。`python` 不可用时改 `python3`。

# 自检清单（逐条可勾选；完整版见 references/checklist.md）

- [ ] result.json 合法，含 case_id、output_contract 全部字段 + 信封字段（status/sources/uncertainty/pending_human/change_log）。
- [ ] 七要素齐备且非空（缺材料时已在 uncertainty 标 missing 并降 partial）。
- [ ] 立得住：问题具体、有可证伪判据；背景 known/unknown/gap 指向同一缺口。
- [ ] 对得上：每个目标 `answer_to` 指向存在问题；路线依赖无悬挂；成果考核方式与目标对应。
- [ ] 排得开：阶段 period 落在总周期内、里程碑与资源匹配、无起止倒置。
- [ ] 说得清：每条风险有 trigger/mitigation/decision_point；关键论断 sources 带 locator+quote。
- [ ] 事实忠实材料：无新增材料外事实；fact/summary/inference 区分正确；推断入 uncertainty。
- [ ] `check` 通过（FAIL=0，WARN 已说明）；`render` 已生成 proposal.md/.mmd/.svg；artifacts 指向真实文件。
- [ ] change_log 记录从骨架到成品的增量；前 3 分钟已落盘骨架。

# 降级策略

- 材料缺失/无法解析：不补造内容；受影响要素留空或标 `kind:inference`，写入 `uncertainty`（kind=missing），status 降为 partial，summary 后写 `limitations`。
- 信息矛盾：并列两个版本并标 `kind=conflict`、指明各自出处，建议人工裁决，写入 `pending_human`。
- 格式/软件不支持：优先降级为 Markdown+Mermaid+SVG（纯标准库可产出）；需 Office 而运行时工具不可用时，交付 Markdown 并在 `degradations` 注明"目标格式未生成，需运行时渲染工具"。
- 周期过短/指标不可达/学科规范冲突/引用无法获取：在 `uncertainty` 或 `pending_human` 指出触发点，不擅自改口径。
- 要素爆炸：只保留与问题/结论/任务直接相关的内容，其余并入 note 或标 `out_of_scope`。
- 权限/运行失败：status=failed 或 partial，在 `degradations`/`change_log` 说明；**绝不编造评测分数、软件状态或科学数据**。
- budget 用尽：以最近一次 check 通过的 partial 结果为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例）

输入 case.json 片段：

```json
{"case_id":"demo","output_contract":{"title":{"required":true},"summary":{"required":true},"proposal":{"required":true},"evidence_map":{"required":true},"artifacts":{"required":true}},"constraints":{"period_months":12}}
```

执行：`python scripts/main.py init --case /input/case.json --out /app/output/result.json`，回填七要素后 `python scripts/main.py check ...`，再 `python scripts/main.py render ...`。示意 result.json 节选（标注为示例）：

```json
{"case_id":"demo","status":"partial","title":"示例：某开题方案","proposal":{"background":{"known":[],"unknown":[],"gap":null},"research_questions":[],"objectives":[],"technical_route":[],"phase_plan":[],"risks":[],"expected_outcomes":[]},"sources":[],"uncertainty":[{"item":"result","issue":"示例：未分析材料","kind":"missing","action":"回填"}],"pending_human":[],"change_log":[{"at":"2026-01-01T00:00:00Z","action":"init skeleton","reason":"示例"}]}
```
