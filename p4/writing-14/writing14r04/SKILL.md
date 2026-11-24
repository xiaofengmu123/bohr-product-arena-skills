---
name: weekly-skill
description: 开题报告与研究计划书 Skill。从 /input/case.json 与 task.md 指定的科研材料中抽取事实、约束与已知结论，生成研究背景与缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果七要素齐全、且与给定周期/经费/数据/人员相容的开题方案；产出可编辑稿件与逐条可回溯的证据映射（locator + 逐字 quote），并显式标注材料缺口、冲突、推断与待人工确认项，缺数据不编造。用于平台官方 DeepSeek Harness 的 3 个固定科研案例。
version: 1.1.0
---

# 角色与何时使用

你是"开题报告与研究计划书"任务的执行者。当运行环境提供 `/input/case.json` 与 `/input/task.md`、且任务要求依据科研材料产出**七要素齐全、事实与来源一致、可继续编辑**的开题方案时，按本 Skill 执行。

四个质量主轴（评分核心）：**立得住**（背景是否真正指向缺口，而非综述堆砌）、**对得上**（目标、技术路线、验证方式、预期成果一一对应）、**排得开**（阶段计划落在给定周期/经费/数据/人员条件内，里程碑与任务量匹配）、**说得清**（风险有触发信号与备选方案，论断与引用逐条可回溯）。

## 何时不使用
纯问答、无材料输入、或题目要求其他体裁且与开题无关时，按 task.md 直接处理；本 Skill 的七要素与证据规范不强制套用。

# 输入契约

- 任务说明：`/input/task.md`（必读，先于任何假设）。
- 案例输入：`/input/case.json`，可能含 `case_id`/`id`、`output_contract`（或 `output_schema`/`required_fields`/`contract`/`result_schema` 等同义键）、材料路径、受众/文体/格式约束、周期/经费/人员/数据条件。
- 科研材料：按 case.json 与 task.md 指定路径读**真实文件**。PDF 用解析文本读取，不假设标签内容；解析层缺失时用运行时 PDF 解析/OCR 工具，扫描件用 OCR 工具。
- 运行目录：中间产物放 `/app/work/`；最终产物与可编辑产物放 `/app/output/`。
- 资源入口：`environment/resources.yaml`（若存在），以 pinned 镜像为准，不虚构软件状态。

# 输出契约

必须产出 `/app/output/result.json`，包含：

1. `case_id`：与 case.json 的 `case_id`/`id` 一致。
2. **题目 `output_contract` 全部字段**：优先从 case.json 提取字段名并逐一回填；未知字段给 `null`/`[]`，不改名、不删除；契约未声明时用默认字段 `title`、`summary`、`proposal`、`evidence_map`、`sources`、`artifacts`。
3. 信封字段：`status`（`complete`/`partial`/`insufficient_evidence`/`input_error`/`failed`）、`sources`（证据清单）、`uncertainty`（不确定/冲突/推断项）、`pending_human`（待人工确认）、`change_log`（修改记录），以及 `errors`/`degradations`/`limitations`/`runtime`。
4. 主产物 `proposal`：七要素结构化对象（背景缺口、研究问题、目标、技术路线、阶段计划、风险、预期成果），字段与词表见 `references/output-contract.md`。
5. 可编辑产物：`scripts/main.py render` 生成 `proposal.md`、`evidence_map.json`、`proposal_preview.html`；题目要求 docx/pptx 时用运行时 `office-renderer`/`browser-automation` 生成并附真实渲染的 PNG/PDF 预览。全部产物路径写入 `artifacts`。

字段语义、节点/关系词表、source 结构见 `references/output-contract.md`。

# 执行流程（总预算 900 秒、64 次模型请求、每响应 ≤16384 token）

原则：**前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进**；宁可交付诚实的 partial，不可最后无产出。脚本统一用相对路径（`scripts/main.py ...`），在 SKILL.md 所在目录执行；若当前目录不是 Skill 根目录，先定位 SKILL.md 再执行。

- **步骤 0（0–1 分钟）**：读 `/input/task.md` 与 `/input/case.json`；确定 `case_id`、材料路径、体裁/受众/格式约束、`output_contract` 字段、周期/经费/数据/人员条件。落盘骨架：
  `python scripts/main.py init --case /input/case.json --out /app/output/result.json`
- **步骤 1（1–3 分钟）**：读取全部材料（PDF 解析/OCR），按段落建立 `sources` 清单（唯一 id + locator 页码/段落 + 逐字 quote + kind）。回填 `title`、`summary`、`constraints` 与初版 `sources`，用 `update` 增量写回。抽到的周期/经费/数据/人员写入 `constraints`，用于后续"排得开"校验。
- **步骤 2（3–6 分钟）**：抽取七要素——背景缺口（已知/未知/待验证 + gap_statement）、研究问题（1–3 个可证伪）、目标（指标/阈值/样本/对照/时间点/考核方式，挂 linked_question）、技术路线（每步 input/output/depends_on/parallel_with）、阶段计划（阶段/时长/里程碑/资源/关键路径/关联步骤）、风险（data/method/schedule/ethics/compliance 分类，每条含触发信号/影响/备选/决策时点）、预期成果（考核方式 + linked_objective）。每条标 `kind`（fact/summary/inference）与 `sources`；无据连接不写，推断项同步写入 `uncertainty`。
- **步骤 3（6–11 分钟）**：运行 `python scripts/main.py check` 修复 FAIL；运行 `python scripts/main.py render` 生成可编辑产物与预览；题目要求 docx/pptx 时用运行时工具生成并渲染 PNG/PDF 预览，追加进 `artifacts`。
- **步骤 4（11–14 分钟）**：对照 `references/checklist.md` 逐条自检；把材料缺口、冲突、推断写入 `uncertainty`，把动作写入 `change_log`；确定 `status`（有 pending_human 或缺口时降级 partial）；重跑 `check` 确认 FAIL=0。

# 自检清单

交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] result.json 合法，含 case_id 与 output_contract 全部字段 + 信封字段。
- [ ] 七要素齐全且忠于材料，无材料外事实、无张冠李戴的数字/结论。
- [ ] 立得住：gap_statement 由 known/unknown/to_verify 推出，指向明确缺口。
- [ ] 对得上：objective↔linked_question、step↔objective、expected_outcome↔objective 互指且无悬空。
- [ ] 排得开：phase_plan 有时长/里程碑/资源，且与 constraints 的周期/经费/人员相容。
- [ ] 说得清：每条风险有触发信号/影响/备选/决策时点；关键论断与引用一一对应。
- [ ] 每个 fact/summary 项的 sources 都能在 sources 清单找到且带 locator 或 quote；inference 已列入 uncertainty。
- [ ] `check` 通过（FAIL=0，WARN 已说明）；`render` 已生成 proposal.md/.json/.html；artifacts 非空且文件真实存在。
- [ ] 前 3 分钟已落盘骨架；change_log 记录从骨架到成品的增量过程。
- [ ] 材料缺口/冲突/推断已在 uncertainty 说明，待确认项在 pending_human；未编造分数、软件状态或科学数据。

# 降级策略（本版重点：缺数据不编造）

- **材料缺失/无法解析**：不补造内容；受影响项标 `kind: inference` 或从方案移除，写入 `uncertainty`（kind=missing），`status` 降级 `partial`；完全无材料时 `insufficient_evidence`，并在 `limitations` 说明。
- **信息自相矛盾**：并列两个版本并标 `kind: conflict`，各指出处，写 `pending_human` 请人工裁决，不擅自选一个。
- **约束缺失（周期/经费/人员）**：`constraints` 相应字段留 `null`，`uncertainty` 记 kind=missing；不得臆造一个周期或经费数字来"补齐"。
- **指标阈值无据**：阈值留 `null` 或标 `inference`，写入 uncertainty，不编造有据的假阈值。
- **格式/软件不支持**：优先 Markdown + HTML 预览（纯标准库可产出）；需 docx/pptx 而运行时工具不可用时，交付 Markdown 并在 `limitations` 注明"目标格式未生成，需运行时渲染工具"。
- **节点/风险爆炸**：只保留与研究问题直接相关的项，其余并入 note 或写 `uncertainty`（out_of_scope）。
- **权限/运行失败**：如实写 `status=failed` 或 `partial`，在 `limitations` 与 `change_log` 说明原因；**绝不编造评测分数或伪造运行结果**。
- **budget 用尽**：以最近一次 `check` 通过的 partial 结果为准，确保 result.json 始终可解析、含完整信封字段。

# 最小调用示例（示意，非正式案例结果）

输入 case.json 片段：`{"case_id":"demo","output_contract":{"title":{"required":true},"summary":{"required":true},"proposal":{"required":true},"evidence_map":{"required":true},"sources":{"required":true},"artifacts":{"required":true}}}`

执行：`python scripts/main.py init` → 增量 `update` 回填内容 → `python scripts/main.py check` → `python scripts/main.py render`。

示意 result.json（节选，标注为示例）：

```json
{
  "case_id": "demo",
  "status": "partial",
  "title": "示例：某课题开题方案",
  "summary": "示意内容，非正式案例结果",
  "proposal": {"research_questions": [], "objectives": [], "risks": []},
  "sources": [],
  "uncertainty": [{"item": "result", "issue": "示例：未分析材料", "kind": "missing", "action": "回填"}],
  "pending_human": [],
  "change_log": [{"at": "2026-01-01T00:00:00", "action": "init", "note": "示例骨架"}],
  "artifacts": []
}
```
