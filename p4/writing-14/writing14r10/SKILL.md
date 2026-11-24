---
name: weekly-skill
description: 开题报告与研究计划书写作 Skill。从 /input/case.json 与 task.md 指定的科研材料中抽取事实底稿、受众、文体与格式约束，围绕七要素（研究背景与缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果）生成与给定周期/经费/数据/人员条件相容的开题方案，保留论断与引用的逐条溯源，并产出可编辑的 proposal.md 与对齐矩阵、证据映射，最终写 /app/output/result.json（含 output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log）。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.0.0
---

# 角色与何时使用

你是"开题报告与研究计划书"任务的执行者。当运行环境提供 `/input/case.json` 与
`/input/task.md`、且任务要求依据科研材料产出**可复核、可编辑、证据可追溯**的开题
报告或研究计划时，按本 Skill 执行。核心目标对应评分四个重点：**立得住**（问题具体、
可证伪，背景真的指向缺口而非综述）、**对得上**（目标/路线/验证/成果与问题一一对应）、
**排得开**（阶段计划落在给定周期与资源内）、**说得清**（风险有触发信号与备选方案，
论断与引用可回溯）。

何时不使用：纯问答、无材料输入、或任务明确要求与本任务无关的体裁时，按 task.md 直接
处理；本 Skill 的七要素/对齐/证据规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，含 `case_id`、`output_contract`（同义键
  `output_schema`/`required_fields`/`contract`）、材料路径、体裁/受众/格式约束。
- 科研材料：按 case.json 指定路径读真实文件；**PDF 用解析文本读取，不假设标签内容**，
  解析层缺失时用运行时 PDF/OCR 工具，扫描件用 OCR。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在），声明的预装工具以 pinned 镜像为准。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 全部字段**：从 case.json 抽取并逐一回填，缺值给
   `null`/`[]`；契约未声明时用默认字段 `title`、`background`、`research_questions`、
   `objectives`、`technical_route`、`schedule`、`risks`、`expected_outcomes`、
   `citations`、`artifacts`（详见 `references/output-contract.md`）。
3. 信封字段：`status`（`complete`/`partial`/`failed`/`insufficient_evidence`/
   `input_error`）、`sources`（证据清单）、`uncertainty`（不确定项）、
   `pending_human`（待人工确认）、`change_log`（增量记录），另加 `errors`/
   `degradations`/`runtime`。
4. 可编辑产物：`scripts/main.py render` 生成 `proposal.md`（七要素正文）、
   `evidence_map.md`（证据映射）、`alignment.md`（对齐矩阵）；题目要求 docx/pptx 时用
   运行时 `office-renderer`/`browser-automation` 生成并附真实 PNG/PDF 预览。所有产物
   路径写入 `artifacts`。

字段语义、词表、source 结构见 `references/output-contract.md`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版 partial 骨架，之后只做增量改进**；宁可交付诚实的
partial，不可最后无产出。脚本调用统一用相对路径，在 SKILL.md 所在目录执行；仅依赖
Python 标准库，可重复运行、幂等。

- **步骤 0（0–1 分钟）发现与落盘**：读 `/input/task.md` 与 `/input/case.json`，确定
  `case_id`、材料路径、体裁/受众/格式约束、`output_contract` 字段；运行
  `python scripts/main.py discover --input /input`，再
  `python scripts/main.py init --input /input --out /app/output --work /app/work` 落盘
  骨架（status=partial）。输入：task.md、case.json → 输出：`/app/output/result.json` 骨架。
- **步骤 1（1–3 分钟）建 sources 与中间表示**：读取全部材料，按段落/页码建立
  `sources` 清单（locator + 逐字 quote），抽取约束（周期/经费/数据/人员/受众/格式），
  回填 `title`、`summary` 与初版 `sources`。运行一次 `validate` 确认契约字段齐。
- **步骤 2（3–7 分钟）七要素与对齐矩阵**：写 `background`（known/unknown/to_verify）、
  `research_questions`（1–3 个可证伪问题）、`objectives`（指标/目标/判断标准）、
  `technical_route`（输入/输出/依赖）、`schedule`（里程碑→月份）、`risks`（四类：
  数据/方法/进度/伦理合规，含触发信号/影响/备选/决策时点）、`expected_outcomes`
  （考核方式）；每条挂 `sources`，填 `alignment.matrix` 证明"对得上"。事实归
  `kind=fact`，归纳 `summary`，补出 `inference` 并记入 `uncertainty`。
- **步骤 3（7–11 分钟）校验与渲染**：运行 `validate` 修复 FAIL（顺序：契约→七要素→
  对齐→计划可行性→证据→产物）；运行 `merge` 生成可编辑产物并写回 artifacts。需要
  docx/pptx 时用运行时工具生成并渲染预览。
- **步骤 4（11–14 分钟）自检收尾**：对照 `references/checklist.md` 逐条勾选；把材料
  缺口、冲突、推断写入 `uncertainty`，动作写入 `change_log`；确定 `status`；重跑
  `validate` 确认 FAIL=0。输出：最终 `result.json`。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`；脚本校验用
`python scripts/main.py validate`，脚本自检用 `python scripts/selftest.py`）：

- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全非空；论断忠于材料、无材料外事实；引用与论断一一对应。
- [ ] 对齐矩阵有效（question_ref/objective_ref/depends_on 无悬空引用）。
- [ ] 里程碑落在给定周期内；风险按四类分类且含触发信号与备选方案。
- [ ] 每个 fact/summary 的 sources 能在 sources 清单找到且带 locator 或 quote。
- [ ] validate FAIL=0；render 已生成 proposal.md/evidence_map.md/alignment.md；
      artifacts 非空且文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录增量过程；推断项已入 uncertainty。

# 降级策略

- **材料缺失/无法解析**：不补造内容；受影响要素标 `kind=missing` 或移除，写入
  `uncertainty`，`status` 降级为 `partial`/`insufficient_evidence`，并在正文说明
  `limitations`。
- **信息自相矛盾**：并列呈现两个版本并标 `kind=conflict`，指明各自出处，建议人工裁决。
- **周期过短/指标不可达**：`validate` 输出 WARN，方案中标注"与给定条件不相容"，写
  `pending_human` 请求放宽周期/资源，不擅自改条件。
- **格式/软件不支持**：优先降级为 Markdown 正文 + 对齐矩阵 + 证据映射；Office 格式
  工具不可用时交付 Markdown 并注明"目标格式未生成，需运行时渲染工具"。
- **节点/引用规模爆炸**：只保留与问题/结论/任务直接相关的论断，其余并入 note 或
  `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed` 或 `partial`，在 `limitations` 与
  `change_log` 说明；**绝不编造评测分数、软件状态或科学数据**。
- **budget 用尽**：以最近一次 `validate` 通过的 partial 结果为准，确保 result.json
  始终可解析、含完整信封字段。

# 最小调用示例（示意输出，非正式案例结果）

输入 case.json 片段：

```json
{
  "case_id": "demo",
  "output_contract": {
    "title": {"required": true},
    "research_questions": {"required": true},
    "objectives": {"required": true},
    "technical_route": {"required": true},
    "schedule": {"required": true},
    "risks": {"required": true},
    "expected_outcomes": {"required": true},
    "citations": {"required": true},
    "artifacts": {"required": true}
  }
}
```

执行：

```bash
python scripts/main.py discover --input /input
python scripts/main.py init --input /input --out /app/output --work /app/work
python scripts/main.py validate --input /input --out /app/output --work /app/work
python scripts/main.py merge --input /input --out /app/output --work /app/work
```

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：未命名研究计划",
  "research_questions": [],
  "objectives": [],
  "technical_route": [],
  "schedule": [],
  "risks": [],
  "expected_outcomes": [],
  "citations": [],
  "artifacts": ["proposal.md", "evidence_map.md", "alignment.md"],
  "sources": [],
  "uncertainty": [{"item": "proposal", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00Z", "action": "init_skeleton", "reason": "示例"}]
}
```
