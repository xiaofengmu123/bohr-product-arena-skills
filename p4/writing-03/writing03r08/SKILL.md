---
name: academic-format-template-adaptation
description: 面向学术格式与模板适配任务的写作 Skill。当需要依据给定稿件与目标模板完成格式适配、模板规格抽取（认得准）、标题层级/图表/参考文献重排（排得对）、转换内容保真（转得全）、导出校验（交得出），且必须为每条改动与关键结论保留"原文摘录—定位—证据强度"可回溯记录、最终产出 /app/output/result.json 时使用。
version: 1.1.0
---

# 学术格式与模板适配 Skill

## 1. 角色与何时使用

- 角色：学术格式适配的执行器与质检器。目标不是"表面改格式"，而是把一份真实稿件按目标模板改到可交付，并让每一项改动都能被复核"依据在哪、强度多强"。
- 何时触发：`/input/case.json` 要求对给定稿件完成格式适配、模板套用、标题层级/图表/参考文献重排、格式转换或导出，并要求保留来源、修改记录与不确定性时。
- 何时不用：纯创意写作、无材料来源的闲聊扩写、超出给定稿件与模板的领域外推测。

## 2. 输入契约

- 原始输入：`/input/case.json`（含 `case_id`、任务说明、材料清单、`output_contract`、评分证据范围）与 `/input/task.md`（题面与运行时约定）。
- 科研材料：`case.json` 指明的稿件、模板、补充规定或论文。PDF 用 `pdf-text-tool` 抽取全文（必要时 `ocr-tool`、`office-renderer`、`font-discovery`）；实际文件内容才是事实依据，不按文件名或领域标签脑补。
- 工作目录：`/app/work`（中间产物、抽取文本、预览）；最终实际结果与可编辑产物写 `/app/output/`。
- 预算：900 秒、64 次模型请求、单次响应 ≤16384 tokens（含推理）。

## 3. 输出契约

必产 `/app/output/result.json`。字段 = `case_id` + `case.json` 的 `output_contract` 全部字段（缺失时用三段式兜底）+ 以下保留字段：

- `status`：`partial | complete | failed | insufficient_evidence`，诚实标注。
- `sources`：来源清单（`id/type/title/path/doi/accessed/locator/verification`）。
- `uncertainty`：`claims_requiring_confirmation`、`conflicting_evidence`、`material_gaps`、`limitations`。
- `pending_human`：需人工确认的项。
- `change_log`：`[{ts, action, detail}]`，每次落盘追加一条。

三段式兜底字段：`deliverable`（目标产物）、`fact_source_map`（事实与来源映射）、`version_boundary`（版本与边界）。本包额外建议产出 `style_spec`（模板规格检查项清单，见 `references/template-rubric.md`），作为"认得准"的核验证据。每个案例还需产出可编辑产物（`.docx`/`.tex`/`.md`）及其渲染预览（PDF/PNG）到 `/app/output/`，并把路径回填进 `deliverable`。

## 4. 执行流程（900 秒 / 64 请求）

硬约束：**前 3 分钟（180 秒）内必须先落盘一版 `result.json` 骨架，之后只做增量改进，不得整体重建。**

- 阶段 0 盘点（0–60s，≤2 请求）：读 `case.json` + `task.md`，解析 `case_id`、`output_contract`、材料清单，列出 `/input` 文件。输出：任务理解 + 材料清单。
- 阶段 1 骨架落盘（≤180s 前完成，≤1 请求）：`python scripts/main.py scaffold --case /input/case.json --out /app/output/result.json`，确认 `case_id` 正确、契约字段齐全。输出：`result.json`（`status=partial`）。
- 阶段 2 规格抽取·认得准（60–300s，≤20 请求）：从模板/规定抽取页面、字体、标题层级、图表、参考文献、页眉页脚、导出七类规格，逐项填 `style_spec`（每项带 `locator+quote`）；版本差异与冲突并列不隐藏。输出：`style_spec`。
- 阶段 3 内容锚定·排得对/转得全（180–540s，≤20 请求）：把稿件内容单元按 `unit_id/quote/locator/transform` 锚定，生成 `fact_source_map`；样式映射、编号、交叉引用、参考文献重排逐条记入 `version_boundary.diff`；冲突证据并列。输出：`fact_source_map` + `version_boundary`。
- 阶段 4 成稿与导出·交得出（360–780s，≤15 请求）：按模板生成 `deliverable.content`；保存可编辑源文件 + PDF/PNG 预览；实测字体嵌入、页码目录、文件可打开性，回填 `export_checks`。输出：`deliverable` + 产物文件。
- 阶段 5 自检收尾（720–900s，≤6 请求）：`python scripts/main.py verify --case /input/case.json --result /app/output/result.json --report /app/output/verification_report.json`；修复硬性错误；写 `status/uncertainty/change_log`；预留 ≤2 请求重试。输出：最终 `result.json` + `verification_report.json`。

每一步都把增量写回 `result.json` 并追加 `change_log`；任一步超时或请求耗尽时保持 `status=partial`，如实写 `limitations`，绝不编造。

## 5. 评分维度对齐（本版核心设计取舍）

本版与通用写作包的区别：把题目五个评分维度逐条落到流程、数据结构与自检项，重点是"认得准/转得全/交得出"的可核验证据链。

| 评分维度 | 落到流程阶段 | 落到数据结构 | 落到自检项 |
|---|---|---|---|
| 实际效果 40 | 阶段 2–4 四问闭环 | `style_spec` + `content_units` + `deliverable` | checklist A 逐条 |
| 使用增益 25 | 阶段 1 先 partial、后增量 | `change_log` + `verification_report.json` | checklist B 逐条 |
| 创新增益 15 | 阶段 2 反推规格、阶段 3 内容锚定 | `style_spec`（反推清单）+ `unit_id+quote` 锚定 | checklist C 逐条 |
| 可靠体验 10 | 阶段 0/5 容错与自检 | `uncertainty` + `pending_human` + `status` | checklist D 逐条 |
| 生态贡献 10 | 全程可复用规则 | `references/` + 最小调用示例 | checklist E 逐条 |

## 6. 自检清单（摘要）

完整逐条清单见 `references/checklist.md`；交付前按 A–E 五组勾选，并对齐 `scripts/main.py verify` 的 `checks` 输出。硬性失败项（`case_id` 不一致、缺契约/保留字段、占位符残留、JSON 无效）必须修复，无法修复则保持 `partial` 并写明 `limitations`。

## 7. 降级策略

- 缺模板/只有摘要/全文不可得：如实记入 `uncertainty.material_gaps`，只写"材料表明"，不补写未提供的规格值。
- 缺软件（`pdf-text-tool/latex/office-renderer/font-discovery` 不可用）：标准库脚本仍可读纯文本；标注"预览未生成/依赖不可用"，不伪造渲染文件。
- 模板版本冲突/规定矛盾：全部列入 `conflicting_evidence`，正文用"存在争议/尚不明确"，不偏向单边。
- 字体缺失：按题面指定替代字体（Liberation Serif），并记入 `limitations`。
- 无法校验来源真实性：`sources` 标 `verification="unverified"`，`change_log` 记录核验路径。
- 硬性失败（缺 `case_id`/字段/JSON 无效）：`main.py verify` 退出码 2，先修复再继续。
- 任何情况禁止编造评测分数、运行结果、文献元数据或科学数据。

## 脚本与资源

- `python scripts/main.py scaffold [--case /input/case.json --out /app/output/result.json]`：骨架落盘（幂等、增量合并）。
- `python scripts/main.py verify [--case … --result … --work /app/work --material-root <dir> --report …]`：契约 + 摘录可定位 + 占位符自检，退出码 0/1/2。
- `python scripts/selftest.py`：端到端自检（正常/坏 JSON/缺契约/幂等四场景），交付前必跑。
- `references/template-rubric.md`：四问方法论与规格检查项结构。
- `references/evidence-map-schema.md`：证据映射、证据强度口径与版本边界规范。
- `references/checklist.md`：评分维度对齐的自检清单。

脚本仅依赖 Python 标准库；在 `/skills/weekly-skill` 根目录以相对路径调用。
