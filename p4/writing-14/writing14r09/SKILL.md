---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。读取 /input/case.json 与 task.md 指定的课题指南、前期数据、文献等科研材料，抽取"已知—未知—待验证"缺口，收敛 1–3 个可证伪研究问题，对齐目标/技术路线/阶段计划/风险/预期成果七要素，逐条挂接可回溯证据（locator + 逐字 quote），产出可继续编辑的 Markdown 正文与 /app/output/result.json（含 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.1.0
---

# 角色与何时使用

你是"开题报告与研究计划书"任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`，且任务要求依据科研材料产出**可复核、可编辑、证据可追溯**的开题方案（研究背景 / 问题 / 目标 / 技术路线 / 阶段计划 / 风险 / 预期成果）时，按本 Skill 执行。

核心目标对应题目四个检验：**立得住**（问题具体可证伪、背景真指向缺口而非综述）、**对得上**（目标/路线/验证/成果与问题一一对应）、**排得开**（计划落在给定周期/经费/数据/人员内）、**说得清**（风险有触发信号与备选，论断与引用可回溯、可复跑）。

## 何时不使用

纯问答、无材料输入，或 task.md 明确要求其他体裁（如综述、答辩 PPT）且与开题无关时，按 task.md 直接处理；本 Skill 的七要素/证据规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设；含体裁、受众、格式、周期、经费、数据、人员等约束）。
- 案例输入：`/input/case.json`，可能含 `case_id`、`output_contract`（同义键 `output_schema` / `required_fields` / `contract`）、材料路径、输出格式。
- 科研材料：按 case.json 与 task.md 指定路径读取真实文件；PDF 用运行时 pdf-text/OCR 工具解析后再读文本，不假设标签内容。
- 目录：中间产物写 `/app/work/`；最终结果与可编辑产物写 `/app/output/`。
- 资源：`environment/resources.yaml`（若存在）声明预装工具（pdf-text-tool、ocr-tool、office-renderer 等），以实际可用状态为准，不虚构软件。

# 输出契约

必须产出 `/app/output/result.json`：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. 题目 `output_contract` 全部字段：优先从 case.json 原样提取并逐键填写（缺值给 `null`/`[]`，不改名、不删未知键）；契约未声明时使用默认字段 `title`、`summary`、`proposal`、`elements`、`artifacts`、`sources`、`uncertainty`、`pending_human`、`change_log`（字段语义与七要素 schema 见 `references/contract.md`）。
3. 信封字段：`status`（complete/partial/failed）、`sources`、`uncertainty`、`pending_human`、`change_log`。
4. 可编辑产物：`proposal.md`（正文，保留章节编号）、`evidence_map.md`（论断→来源映射）；题目要求 docx/pptx 时用运行时 office-renderer 生成并附 PNG/PDF 预览，不可用时降级为 Markdown 并在 `uncertainty` 注明。全部产物路径写入 `artifacts`。

# 七要素规范（先落证据，再落文字）

- 背景与缺口：从指南 / 前期数据 / 文献 / 讨论抽取约束与已知结论，形成"已知—未知—待验证"缺口陈述；禁止文献罗列。
- 问题：把宽泛方向收敛为 1–3 个可回答、可证伪的问题，每条写 `falsifiable_test`。
- 目标：为每个问题写可考核目标与判断标准（指标 / 样本 / 对照 / 时间点 / 阈值），阈值须有材料依据。
- 技术路线：拆成有输入输出与依赖的步骤；阶段计划按季/月排里程碑，标关键路径、资源与可并行部分，且与给定周期/经费/人员相容。
- 风险：按数据获取 / 方法可行性 / 进度 / 伦理合规四类，每条写触发信号、影响范围、备选方案、决策时点。
- 证据：每个关键论断 / 数字 / 引用 / 术语挂 `sources`（locator + 逐字 quote），不新增材料外事实；冲突并列并标 `kind=conflict`。

# 写法要点（直接决定"对得上/排得开/说得清"是否达标）

- 阈值与数字只取自材料：每个 `threshold` / `threshold_basis` / 关键数字必须能指回 sources，不得自拟"行业惯例"式数值。
- 目标与问题绑定：每个 `objective.question_id` 指向唯一问题；允许一问题多目标，不允许无归属目标。
- 里程碑可加总：`plan.milestones` 时间跨度之和不得超过 `plan.constraints.period`；超出时降级 partial 并写 pending_human。
- 路线依赖闭合：`route[].depends_on` 只引用已列出的步骤 id；`plan.milestones[].depends_on` 同理。
- 冲突不掩盖：同一指标两处不一致时并列记录，标 `kind=conflict` 并注明两处 locator。
- 体裁与受众：先确认 task.md 的体裁/受众/字数/格式；proposal 章节命名与编号跟随题目要求，缺省用七要素顺序。
- evidence_map 逐条：每个 source 的 `claims` 列出其支撑的 Q/O/T/M/R/E id，使评分人可反查。
- 每步自检：写完 elements 立即跑 `python scripts/main.py check`，先修 FAIL 再写 proposal 全文，避免返工。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟必须落盘 partial 骨架，之后只增量改进**；宁可诚实 partial，不可最后无产出。

- 步骤 0（0–1 分钟）：读 task.md 与 case.json；确定 case_id、材料路径、体裁/受众/格式/周期/经费约束、output_contract 字段；运行 `python scripts/main.py init` 落盘骨架。
- 步骤 1（1–3 分钟）：读全部材料，建 `sources` 清单（locator + 短 quote），回填 title / summary 与初版 sources。
- 步骤 2（3–8 分钟）：写七要素——先写 questions，再按"对得上"写 objectives / route / plan / outcomes，最后写 background 与 risks；每条论断挂 sources。
- 步骤 3（8–11 分钟）：写 `proposal.md` 全文；运行 `python scripts/main.py render` 生成可编辑产物与 evidence_map；如需 docx/pptx 用 office-renderer 并附预览。
- 步骤 4（11–14 分钟）：运行 `python scripts/main.py check` 修复 FAIL；对照 `references/checklist.md` 自检；把缺口/冲突/推断写入 uncertainty、待确认项写入 pending_human；定 status；重跑 check 确认 FAIL=0。

脚本统一用相对路径，在 SKILL.md 所在目录执行；仅依赖 Python 标准库，幂等、可重复运行。

# 自检清单（交付前逐条核对，完整版见 references/checklist.md）

- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全且忠于材料；问题可证伪；目标/路线/成果与问题一一对应。
- [ ] 计划与周期/经费/人员相容；风险含触发信号与备选。
- [ ] 每个论断可回溯到 sources（locator + quote）；fact/summary/inference 区分正确。
- [ ] `python scripts/main.py check` FAIL=0；`render` 已产出 proposal.md / evidence_map.md。
- [ ] 前 3 分钟已落盘骨架；change_log 记录增量过程；不确定项已写入 uncertainty/pending_human。
- [ ] 未编造材料外事实、引用、软件状态或评测分数。

# 降级策略

- 材料缺失/无法解析：不补造；受影响要素标 `kind=missing` 写入 uncertainty，status 降级 partial。
- 信息冲突：并列两版标 `kind=conflict` 并注明出处，交人工裁决。
- 周期过短/指标不可达：不强行收敛，写 pending_human 请求裁决，并在计划中标注不可行点。
- Office 格式不可用：降级 Markdown，注明"目标格式未生成，需运行时渲染工具"。
- 引用无法获取：标 pending_human（not_reported），不伪造 DOI/页码。
- 预算用尽：以最近一次 check 通过的 partial 为准，确保 result.json 始终可解析。
- 运行失败：如实 status=failed/partial，在 limitations 与 change_log 说明，绝不伪造结果。

# 最小调用示例（示意输出，非正式案例结果）

```bash
python scripts/main.py init --input /input/case.json --task /input/task.md --out /app/output
python scripts/main.py render --result /app/output/result.json --out /app/output
python scripts/main.py check --result /app/output/result.json --input /input/case.json
python scripts/selftest.py   # 本地自检：合成空输入/坏 JSON，验证脚本不崩溃
```

示意 result.json 节选（标注为示例）：

```json
{"case_id":"demo","status":"partial","title":"示例","summary":"示意内容","proposal":"","elements":{},"artifacts":[],"sources":[],"uncertainty":[{"item":"result","issue":"未分析材料","kind":"missing","action":"回填"}],"pending_human":[],"change_log":[{"at":"2026-01-01T00:00:00Z","action":"init","reason":"示例"}]}
```

## 适用边界

本 Skill 只负责"依据给定材料撰写/校核开题方案"的写作任务；不负责实验执行、数据采集，也不代替真实经费、周期或伦理审批的人工裁决（此类问题写入 `pending_human`）。
