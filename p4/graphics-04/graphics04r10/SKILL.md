---
name: figure-model-skill
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取带逐字证据的科学实体、连接关系、步骤顺序与材料/能量/信息流向，先解析 output_contract 并落盘可核验的 result.json 骨架，再构建 FigureModel 中间表示（IR），据此确定性布图并生成可编辑分层 SVG（装置图、机制图、流程图、技术路线图），完成契约→中间表示→渲染三级自检与真实 PNG/PDF 导出，最终交付可追溯、可复跑的 /app/output/result.json。
version: 1.0.0
---

# figure-model-skill：契约驱动的科研图稿生成与核验

## 1. 角色与何时使用
本 Skill 让 Agent 把科研材料变成一张"画得对、改得动、能复跑"的图稿，并同时交付可核验的 result.json。核心不是直接画图，而是**先解析 output_contract、再抽取带逐字证据的 FigureModel 中间表示（IR）、据此确定性布图、生成可编辑分层 SVG、真实渲染预览，并对契约/中间表示/渲染做三级自检**。
- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数值拟合与统计分析；判断实验方案本身是否科学合理；论文排版与投稿格式适配。
- **判断标准**：图上每个实体能否追溯到输入材料，实体间连接与先后顺序是否成立，交付物能否被他人打开、修改并复跑出同一张图。

## 2. 输入契约（运行时）
- 原始输入固定为 `/input/case.json`（案例说明、输入约束、`output_contract`）与 `/input/task.md`（题面）；科研材料与图件按 `case.json` 指引读取。
- PDF 用 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件，不依据文件名标签假设内容；图表数值以实际源数据为准。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`。
- 脚本在本 Skill 根目录 `scripts/` 下：先 `cd /skills/weekly-skill`，用相对路径调用，例如 `python scripts/main.py init ...`。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8 JSON），字段如下：
- `case_id`：取自 `/input/case.json` 的 `case_id` 或 `id`，缺省 `unknown`。
- 若 `case.json` 声明了 `output_contract`，按字段名**原样输出全部字段**（缺值给 `null` 或 `[]`，不改名、不删未知键），再补通用字段；未声明时采用默认六类字段：
  - `entities`：科学实体（id、label、type、source_ref、quote、note）。
  - `relations`：空间/流向/顺序关系（from、to、kind、direction、source_ref、quote）。
  - `data_encoding`：数据映射（数值/参数 → 尺寸、颜色、线宽、编号；field、mapping、target、source_ref、quote）。
  - `legend_annotations`：图例与标注映射（key → 颜色/线型/符号及文字；含 source_ref）。
  - `editable_sources`：可编辑源文件清单（path、format、layers、notes）。
  - `exports`：导出件清单（path、format、width、height、dpi、fonts、color_check、render_status）。
- 通用字段：`status`（in_progress|complete|partial|failed）、`sources`（证据定位：文件+页码/段落）、`uncertainty`、`pending_human`、`change_log`、`limitations`、`checks`、`artifacts`。
- 关键约定：可编辑源文件优先交付**分层 SVG**；另交付真实渲染的 PNG/PDF 预览；每个实体/关系/图例/标注均带 `source_ref` 与逐字 `quote`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）
**硬性要求**：前 3 分钟（约 180 秒）内必须先落盘 result.json 骨架（status=in_progress，含 case_id、全部契约字段空值、首条 change_log），之后只做增量改进；任何时刻均可交付诚实的部分结果。每步给出"输入 → 输出与时间预算"：

1. **读输入并解析契约（≤90s）**：读 `/input/case.json` + `/input/task.md` → 确定 case_id、图种 kind、材料路径、output_contract 字段。输出：契约键列表。
2. **落盘骨架（≤60s，累计须 <180s）**：`python scripts/main.py init --case /input/case.json --work /app/work --out /app/output` → 生成 result.json 骨架。
3. **抽取 FigureModel IR（≤150s）**：从材料抽取 entities/relations/steps/data_encoding/legend_annotations，逐条附 `source_ref` 与逐字 `quote` → 写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。
4. **契约与 IR 前置自检（≤60s）**：`python scripts/main.py validate --ir /app/work/ir.json --result /app/output/result.json --out /app/work/checks.json` → 契约键齐全 + 断头连接/方向/source_ref 覆盖，critical 项先修。
5. **确定性布图生成 SVG（≤150s）**：`python scripts/main.py build --ir /app/work/ir.json --out /app/work/figure.svg`；坐标缺失时脚本用确定性网格布局并在 metadata 标 `auto-layout`，模型可微调坐标后重跑。
6. **渲染预览（≤120s）**：用 `browser-automation` / `office-renderer` / `pdf-render-tool` 把 SVG 导出 PNG/PDF，复制到 `/app/output/`；失败写入 limitations，不编造。
7. **SVG 后置自检与收尾（≤150s）**：`python scripts/main.py validate --svg /app/work/figure.svg ...` 校验图层/箭头/画布；`python scripts/main.py merge --work /app/work --out /app/output` 写入 entities/relations/sources/uncertainty/checks；`python scripts/main.py finalize --out /app/output --status complete|partial` 写状态与 artifact 清单。

剩余约 120 秒用于增量改进（修正连接方向、补图例、重渲染、复核 source_ref），每次改动后更新 change_log。

## 5. 自检清单（逐条对应评分维度）
- [ ] 实际效果·完成度：交付可编辑分层 SVG + 真实渲染 PNG/PDF，不是纯文字或单张截图。
- [ ] 实际效果·正确性：部件名称/数量/接口与材料一致；连接方向正确、箭头无反；数值标注与来源一致。
- [ ] 实际效果·完整性：entities / relations / data_encoding / legend_annotations / editable_sources / exports 六类齐全。
- [ ] 实际效果·可核验：SVG 含 source_ref 与 quote；result.json 含 sources 与 checks，可独立复跑。
- [ ] 使用增益：IR → 契约校验 → 布图 → 自检流水线可复用，返工与人工修改量下降。
- [ ] 创新增益：沉淀契约优先解析、FigureModel 中间表示与三级校验顺序（见 `references/ir_schema.md`）。
- [ ] 可靠体验：脚本仅标准库、相对路径、异常有降级；确定性布局保证重复运行结果一致。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。
提交前运行 `python scripts/selftest.py`，要求退出码 0。

## 6. 降级策略（缺数据/缺软件时如实标注 limitations，禁止编造）
- 材料缺失或无法解析：在 `limitations` 与 `uncertainty` 标注"无法判断"，不补造部件、数值或来源。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `fonts` 字段标注并降级英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 `change_log` 与 `limitations`。
- 坐标/接口冲突：标 `uncertainty`，优先遵循原始材料原文，不改写科学内容。
- 空输入目录：退出码 0，写 `status=partial` 的合法 result.json，不抛异常。

## 7. 脚本与资源（相对路径，仅标准库）
- `scripts/main.py`：薄编排层，子命令 `init` / `build` / `validate` / `merge` / `finalize`。
- `scripts/core_impl.py`：纯函数库——契约归一化、IR 校验、确定性布局、分层 SVG 生成、SVG 校验、result 合并。
- `scripts/selftest.py`：自检脚本，构造最小案例跑通全流程并断言结果结构与 SVG 可解析。
- `references/ir_schema.md`：FigureModel 字段、图元、布图与三级校验规范。
- `references/checklist.md`：可勾选检查清单与常见失败处置。
