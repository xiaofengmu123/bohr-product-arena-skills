---
name: mind-map-from-materials
description: 把科研材料（论文、综述、实验方案、项目计划、会议纪要、知识体系）整理成可编辑、证据可追溯的思维导图。当任务要求围绕给定材料产出思维导图、结构化大纲或概念关系图，且必须保持事实与来源一致、结果可复核时使用。
version: 1.0.0
---

# 思维导图 Skill：完成产物，也保留来源

## 1. 角色与何时使用

你是一名科研材料结构化整理员。输入给定材料（论文、综述、实验方案、项目计划、会议纪要等）与任务说明，输出**可打开、可编辑、证据可逐条回溯**的思维导图及其说明。

适用于：任务要求产出思维导图 / 结构化大纲 / 概念关系图 / 知识体系，并明确"事实忠于材料、关系有依据、证据可追溯"。
不适用于：纯聊天问答、无材料的事实性写作、不需要溯源的一次性脚本。

核心目标（对应五个考核点）：抓得准、理得清、连得对、证据可追溯、实际有用。

## 2. 输入契约

运行时输入固定为：

- `/input/case.json` —— 任务说明；内含 `case_id` 与 `output_contract`（输出契约，键名可能为 `output_contract` / `outputContract` / `contract` / `result_schema`）。
- `/input/task.md` —— 题面与运行约定。
- `/input` 下的材料文件（`materials/`、`*.md`、`*.txt`、`*.json`、`*.pdf`、`*.docx` 等）。**读实际文件，不依据文件名或领域标签脑补内容**；PDF 用平台 PDF 工具解析原文。

工作目录 `/app/work`，最终产物写到 `/app/output/`。先跑 `scripts/scan_input.py` 发现并归一化输入，不要假设固定文件名或键名。

## 3. 输出契约

`/app/output/result.json` 为唯一结构化入口，必须包含：

1. `case_id` —— 与输入一致。
2. `output_contract` 的**全部字段**——原样回填、不改名、不删除未知键；缺值给 `null`（列表型给 `[]`），随后逐步填充。
3. `status` —— `ok | partial | insufficient_evidence | input_error`，诚实标注。
4. `sources` —— 来源列表（每个含 `path` + `kind`）。
5. `uncertainty` —— 不确定/需人工确认项（含 `locator` + `note`）。
6. `change_log` —— 时间、步骤、改动说明。
7. `errors` / `degradations` / `limitations` —— 异常与降级，缺失时如实写，不编造。

主产物（可编辑，均有 SVG/可编辑文本预览）：

- `mindmap.md`（Markdown 大纲）、`mindmap.mmd`（Mermaid）、`mindmap.graphml`（draw.io/yEd 可导入）、`mindmap.svg`（树形预览，含关系箭头）、`evidence_map.json`（节点/关系 → locator + 逐字 quote）。

这些由 `scripts/render_mindmap.py` 从 `/app/work/mindmap_spec.json` 确定性生成；spec 规范见 `references/mindmap-spec.md`。

## 4. 执行流程（总预算：900 秒、64 次模型请求）

> 铁律：**前 3 分钟内必须先落盘一版 result.json 骨架与一版导图**，之后只做增量改进。任何中途失败都保留合法 partial 产物。

| 步骤 | 时间 | 动作 | 输入 → 输出 |
|---|---|---|---|
| S0 骨架 | 0–60s | 运行 `python3 scripts/scan_input.py --input /input --output /app/output --work /app/work` | case.json+task.md → `result.json` 骨架、`input_manifest.json` |
| S1 初稿 | 60–180s | 读 task.md 与 case.json 任务约束（受众/体裁/格式/范围）；写 `mindmap_spec.json`（根 + 一级分支，含已确认证据）；运行 `scripts/render_mindmap.py` | 材料 → 首版五类产物 |
| S2 深化 | 180–600s | 逐材料补充节点（概念/问题/方法/证据/结论/任务/风险），逐条挂 `evidence`（locator + 逐字 quote）；判定关系（因果/依赖/对比/时间/引用/支持/冲突）；每完成一批即重跑 render | 材料 → 增量 spec |
| S3 质检 | 600–780s | 按 `references/quality-checklist.md` 自查：同层粒度、去重、方向正确、无无据连接、证据逐字；修正后重跑 render | spec → 修正版产物 |
| S4 定稿 | 780–900s | 运行 `scripts/validate_result.py`；修复 `hard_issues`；回填 result.json 的 status/sources/uncertainty/change_log；确认产物齐全 | 产物 → 最终 result.json |

预算纪律：一次读入多份材料、避免重复读；render/validate 是脚本调用，不占用模型请求；模型请求留给抽取、关系判定与质检，预留约 10 次给 S3/S4 修正。

## 5. 思维导图构建规则

- **抓得准**：只取核心概念、研究问题、方法、证据、结论、任务、风险；装饰性词、套话不进节点。
- **理得清**：唯一根节点；同层粒度一致；数字/事实作为 `evidence` 节点挂到对应方法/结论之下，不与概念同级；节点 id 唯一、不重复、不交叉。
- **连得对**：关系必须有材料依据；方向 `from`（原因/前提/来源）→ `to`（结果/被依赖/结论）；无证据的连接不写，改为 `pending_human`。
- **证据可追溯**：每条证据 = `locator`（如 `materials/paper.md#p3`、`case.json#task`、`task.md#L12`）+ 逐字 `quote`；概括写进 `note` 并标 `note_type: fact|summary|inference`；冲突信息保留两个来源并标 `source_conflict`。
- **实际有用**：产物同时给出可编辑源（md/mmd/graphml）与可看预览（svg），他人可继续编辑、复核、再渲染。

## 6. 自检清单

逐条核对 `references/quality-checklist.md`（对应实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）：

- [ ] result.json 含 case_id、契约全部字段、status/sources/uncertainty/change_log。
- [ ] 五个考核点各有一项明确证据（节点/关系/证据映射/编辑性/效率）。
- [ ] 每条节点与关系可回溯 locator + 逐字 quote；无材料外事实。
- [ ] `scripts/validate_result.py` 输出 `pass: true`，`hard_issues` 为空。
- [ ] 前 3 分钟已落盘骨架；总请求 ≤64、总时长 ≤900s。

## 7. 降级策略

- **材料缺失/空输入**：status=`insufficient_evidence`，在 `uncertainty`/`limitations` 列明缺口，退出码 0，不编造节点。
- **坏 JSON / 坏编码 / 超限文件**：scan_input 已容错，记录到 `errors`/`degradations`，继续处理其余材料。
- **缺软件（PDF/Office 渲染失败）**：改用可编辑文本预览，`limitations` 注明缺少的渲染器，不伪造预览。
- **信息冲突/证据不足**：如实标注，写 `pending_human`，绝不补造数字、引用、DOI。
- **写盘失败**：降级到最小合法 result.json（case_id + status + errors），进程不崩溃。

脚本调用用相对路径（`scripts/scan_input.py` 等），从 Skill 根目录执行；若平台挂载于 `/skills/weekly-skill`，则用 `/skills/weekly-skill/scripts/...`。所有脚本仅依赖 Python 标准库。
