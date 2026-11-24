---
name: tracefigure-skill
description: "面向科研绘图的“评分维度对齐”Skill：把题目五项评分维度（实际效果、使用增益、创新增益、可靠体验、生态贡献）逐条映射到执行步骤与自检项；从科研材料抽取“实体—连接—步骤—流向—数据—图例”证据中间表示 IR（每项带 locator 与逐字 quote），据此确定性布图生成分层可编辑 SVG，渲染 PNG/PDF 预览，运行自检并写出可核验的 /app/output/result.json。"
version: 1.0.0
---

# tracefigure-skill：评分维度对齐、可追溯、可复跑的科研绘图

## 1. 角色与何时使用

本 Skill 让 Agent 在画图前先建立可追溯的证据中间表示（IR），再确定性布图、生成分层可编辑 SVG、渲染预览并自检，最终输出可独立复核的 `result.json`。核心不是“画得多漂亮”，而是“每个图元都能指回材料、每条连接与顺序都成立、交付物能被人打开修改并复跑出同一张图”。

- **适用**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图、技术路线图或图表编辑。
- **不适用**：数据分析与数值拟合；判断实验方案本身是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）

- 原始输入固定位于 `/input/case.json` 与 `/input/task.md`；科研材料与图件按 `case.json` 指引读取，不依据文件名或领域标签假设内容。
- PDF/图片用平台工具实际解析：`pdf-text-tool` / `pdf-render-tool` / `ocr-tool`；图表数据以实际源数据为准，不以标签补造。
- 工作目录 `/app/work`，最终产物写到 `/app/output/`（含 `result.json`）。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本位于本 Skill 根目录 `scripts/`：先 `cd /skills/weekly-skill`，用相对路径调用，如 `python scripts/main.py init ...`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，BOM 容错读取、无 BOM 写出）：

- `case_id`：取自 `/input/case.json` 的 `id` 或 `case_id`。
- 若 `case.json` 声明 `output_contract`，按声明字段名**原样回填全部字段**（缺值给 `null`/`[]`，不改名、不删未知键）；未声明时采用默认六类契约字段。
- 默认契约六类要素：
  - `entities`：科学实体（id、label、type、count、source_ref、quote、note）。
  - `relations`：空间/流向/顺序关系（id、from、to、kind、direction、style、source_ref、quote）。
  - `data_encoding`：数值/参数 → 尺寸、颜色、线宽、编号的映射（key、value、mapping、target、source_ref、quote）。
  - `legend_annotations`：图例与标注映射（key → 颜色/线型/符号及文字、source_ref）。
  - `editable_sources`：可编辑源文件清单（path、format、layers、notes）。
  - `exports`：导出件清单（path、format、width、height、dpi、fonts、color_check、render_status）。
- 通用补充字段：`status`（in_progress | complete | partial | failed | insufficient_evidence）、`sources`（文件+页码/段落定位）、`uncertainty`、`pending_human`、`limitations`、`checks`、`artifacts`、`change_log`、`runtime`。
- 关键约定：可编辑源优先交付**分层 SVG**（layer-relations / layer-entities / layer-steps / layer-stages / layer-data / layer-annotations / layer-legend）；另交付真实渲染 PNG/PDF 预览；SVG 中每个实体/关系/图例/标注均带 `data-source-ref`，且 IR 中带逐字 `quote`。

## 4. 执行流程（总预算 900 秒、64 次模型请求；每响应 ≤16384 token）

**硬性要求**：前 3 分钟（约 180 秒）内先落盘 `result.json` 骨架（`python scripts/main.py init`，status=in_progress，含 case_id、空契约字段、首条 change_log），此后只做增量改进，任何时刻都可交付诚实的 partial 结果。每步给出“输入 → 输出与时间预算”：

1. **读输入（≤90s）**：读 `/input/case.json` + `/input/task.md` → 确定 case_id、图种 kind、材料路径、output_contract 字段；列出可用材料文件。
2. **抽取证据 IR（≤150s）**：从材料抽取 entities/relations/steps/stages/data_encoding/legend，每条附 `source_ref`（locator）+ 逐字 `quote` → 写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。
3. **落盘骨架（≤60s，累计必须 <180s）**：`python scripts/main.py init --case /input/case.json --out /app/output/result.json`。
4. **确定性布图生成 SVG（≤180s）**：`python scripts/figure.py build --ir /app/work/ir.json --out /app/work/figure.svg`；坐标缺失时脚本给出确定性网格布局并在 metadata 标注 auto-layout，人工/模型微调坐标后重跑。
5. **渲染预览（≤120s）**：用 `browser-automation` / `office-renderer` / `pdf-render-tool` 将 SVG 导出 PNG/PDF；失败写入 `limitations`，不伪造成功。
6. **自检（≤120s）**：`python scripts/figure.py validate --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json`，按结果修订 IR/SVG（最多 2 轮）。
7. **收尾（≤120s）**：复制产物到 `/app/output/`；`python scripts/main.py merge --work /app/work --out /app/output/result.json` 写入六类契约字段与 sources/uncertainty/checks；`python scripts/main.py finalize --status complete|partial --out /app/output/result.json` 写状态、artifact 清单与结束时间。

剩余时间用于增量改进（修连接方向、补图例、重渲染、复核 source_ref），每次改动追加 `change_log`。

## 5. 评分维度对齐表（本 Skill 的核心设计取舍）

把题目评分维度逐条映射到“哪一步产生 + 哪个自检项验证 + 哪个字段记录”，保证流程不落空、自检可对账：

| 评分维度（分值） | 产生步骤 | 自检项 | result.json 记录字段 |
|---|---|---|---|
| 实际效果·完成度 15 | 步骤 4/5 交付分层 SVG + PNG/PDF | C1 交付物是可编辑 SVG 而非文字/截图 | editable_sources / exports |
| 实际效果·正确性 15 | 步骤 2 抽取 IR + 步骤 4 布图 | C5/C6/C8/C9 名称、数量、接口、方向、编号对齐 | entities / relations |
| 实际效果·完整性 5 | 步骤 2/7 六类要素齐备 | C1 六类契约字段非空且覆盖题目要求 | 六类字段 |
| 实际效果·可核验 5 | 步骤 2/6 证据溯源 | C4 SVG 含 data-source-ref，result.json 含 sources/checks | sources / checks |
| 使用增益·质量 15 | 全程 IR→布图→自检 | C5–C9 相比基础 Agent 返工与错漏下降 | artifacts / change_log |
| 使用增益·效率 5 | 步骤 3/4 骨架先行 + 确定性布图 | C1 前 3 分钟已落盘骨架，重跑结果一致 | runtime / change_log |
| 使用增益·可验证 5 | 步骤 6/7 | C12 运行 selftest 并记录通过项 | checks / artifacts |
| 创新增益 15 | IR 中间表示 + 布图/编号/图例规则 | C11 IR 可复用，规则沉淀于 ir_schema.md | editable_sources / references |
| 可靠体验 10 | 脚本标准库、相对路径、异常降级 | C12/C13 缺参/坏 JSON/空输入不崩溃 | limitations / change_log |
| 生态贡献 10 | SKILL.md + references + 最小示例 | C12 文档齐备、他人可复用 | artifacts / sources |

## 6. 自检清单（交付前逐条勾选）

- [ ] C1 已产出 `/app/output/result.json`，含 `case_id`、`status`、`sources`、`uncertainty`、`change_log` 与六类契约字段。
- [ ] C2 已产出可编辑分层 SVG（layer-entities/layer-relations/layer-steps/layer-legend/layer-annotations）。
- [ ] C3 已产出真实渲染 PNG/PDF 预览；缺渲染工具时已在 `limitations` 说明。
- [ ] C4 每个 entity/relation/annotation/图例均含 `source_ref`，可追溯到输入材料原文，且 IR 含逐字 `quote`。
- [ ] C5 所有 relation 的 from/to 指向存在的实体/步骤/阶段，无断头箭头。
- [ ] C6 箭头方向与材料流向一致，回流用 dashed，无反。
- [ ] C7 图例颜色/线型与图形一致，无“图例与图形不一致”。
- [ ] C8 数值标注与来源一致，无凭空补齐的部件、管路或数值。
- [ ] C9 步骤编号与正文顺序逐条对照。
- [ ] C10 所有实体在画布内，无溢出裁剪。
- [ ] C11 已运行 `scripts/figure.py validate`，critical 项全部通过；未通过项已在 `uncertainty`/`limitations` 说明。
- [ ] C12 已运行 `scripts/selftest.py` 且全部通过（包级自检）。
- [ ] C13 空输入/坏 JSON/缺字体/缺渲染工具等异常已降级处理并如实记录。

## 7. 降级策略（缺数据/缺软件必须如实标注 limitations，禁止编造）

- 材料缺失或无法解析：在 `limitations` 与 `uncertainty` 标“无法判断”，不补造部件、数值或来源。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `fonts` 标注并降级英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 `change_log` 与 `limitations`。
- 坐标/接口冲突：标 `uncertainty`，优先遵循原始材料原文，不改写科学内容。
- 坏 JSON/坏编码/空输入：脚本以 `utf-8-sig` 读取并容错，输出最小合法结果（status=insufficient_evidence），退出码 0。

## 8. 脚本与资源（相对路径，纯标准库）

- `scripts/main.py`：`init`（落盘骨架）/ `merge`（合并 IR 与 checks）/ `finalize`（写状态与 artifact）/ `run`（一键 init→build→validate→merge→finalize）。
- `scripts/figure.py`：`build`（IR→分层 SVG）/ `validate`（SVG/IR 自检，输出 checks.json）。
- `scripts/selftest.py`：包级自检（frontmatter、标准库约束、模块可导入、demo 输入输出、确定性一致）。
- `references/ir_schema.md`：IR 字段、图元、布图、编号、图例与配色规范。
- `references/checklist.md`：可勾选清单与常见失败样例。
