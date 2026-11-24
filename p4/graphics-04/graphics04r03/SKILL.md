---
name: traceable-figure-skill
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取“实体—连接—步骤—流向”结构化中间表示（IR），逐条附 source_ref 与逐字 quote，据此确定性布图并生成可编辑、分层、可追溯的 SVG 图稿，输出真实渲染预览与可复核的 /app/output/result.json；内置 scripts/selftest.py 保证同输入两次产物一致。适用于“画得对、可编辑、可复跑、可自检”的实验装置与技术路线绘图任务。
version: 2.0.0
---

# traceable-figure-skill：可溯源、确定性、可自检的科研绘图

## 1. 角色与何时使用
本 Skill 让 Agent 先把材料解析为结构化中间表示（IR），再做确定性布图、生成可编辑 SVG、渲染预览、自检与收尾，避免“只交文字/截图、凭空补齐管路、箭头反向、图例不一致、无源文件、无证据定位”等问题。
- **使用时机**：输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分，需产出装置图、机制图、流程图或技术路线图。
- **不适用**：数据分析与数值拟合；判断实验方案是否科学合理；论文排版与期刊投稿格式适配。

## 2. 输入契约（运行时）
- 原始输入：`/input/case.json`、`/input/task.md`；科研材料与图件按 `case.json` 指引读取。PDF 用平台 `pdf-text-tool` / `pdf-render-tool` / `ocr-tool` 解析实际文件，不依据标签假设内容；图表数据以实际源数据为准。
- 工作目录 `/app/work`；最终产物写到 `/app/output/`（含 `result.json`）。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 脚本在本 Skill 根 `scripts/`，先 `cd /skills/weekly-skill`，用相对路径调用：`python scripts/main.py <子命令> ...`。

## 3. 输出契约
必须产出 `/app/output/result.json`（UTF-8 JSON），含：
- `case_id`：取自 `/input/case.json`（`case_id` 或 `id`）。
- 若 `case.json` 声明 `output_contract`，按其字段名**原样回填全部字段**（缺值给 `[]`/`null`）；未声明时用默认六类契约字段：
  - `entities`：科学实体（id、label、type、source_ref、quote、note）。
  - `relations`：空间/流向关系（from、to、kind、direction、style、source_ref）。
  - `data_encoding`：数据映射（数值/参数 → 尺寸、颜色、线宽、编号）。
  - `legend_annotations`：图例与标注映射（key → 颜色/线型/符号及文字）。
  - `editable_sources`：可编辑源文件清单（path、format、layers、notes）。
  - `exports`：导出件清单（path、format、width、height、dpi、fonts、color_check、render_status）。
- 通用补充字段（必填）：`status`（in_progress|complete|partial|failed）、`primary_artifact`（主产物路径）、`sources`（证据定位：locator + 逐字 quote）、`uncertainty`、`pending_human`、`change_log`、`limitations`、`checks`、`artifacts`。
- 关键约定：可编辑源文件优先交付**分层 SVG**（layer-relations / layer-entities / layer-annotations / layer-legend）；每个实体/关系/图例/标注均带 `data-source-ref`，可追溯到输入材料原文；另交付真实渲染 PNG/PDF 预览供检查。

## 4. 执行流程（总预算 900 秒、64 次模型请求）
**硬性要求**：前 3 分钟（≈180 秒）内必须先执行第 1 步落盘 `result.json` 骨架（status=in_progress、空契约字段、首条 change_log），之后只做增量改进；任何时刻均可交付诚实部分结果。每步“输入→输出与时间预算”：
1. **读输入（≤60s）**：读 `/input/case.json` + `/input/task.md` → 确定案例目标、图种（kind）、材料路径、output_contract 字段。
2. **落盘骨架（≤60s，累计 <180s）**：`python scripts/main.py init --case /input/case.json --work /app/work --out /app/output`。
3. **提取 IR（≤180s）**：从材料逐条抽取 entities/relations/steps/data_encoding/legend_annotations，每项附 `source_ref`（文件+页码/段落）与 `quote`（逐字引用），写入 `/app/work/ir.json`（字段见 `references/ir_schema.md`）。
4. **布图生成 SVG（≤120s）**：`python scripts/main.py build --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/output --render`。坐标缺失时脚本用确定性拓扑分层+网格布局（meta 标 auto-layout），之后模型可微调坐标重跑；禁止随机布局。
5. **渲染预览（≤120s）**：build 已尽力尝试 cairosvg/rsvg-convert/inkscape，并把结论自动写入 `/app/work/render.json`；仍失败则用 `browser-automation` / `office-renderer` / `pdf-render-tool` 导出 PNG/PDF，导出失败写入 `limitations`，不编造。
6. **自检（≤120s）**：`python scripts/main.py check --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json`；critical 项全部通过，未通过项写入 `uncertainty`/`limitations` 后再修订（最多 2 轮）。
7. **收尾（≤90s）**：`python scripts/main.py finalize --work /app/work --out /app/output --status complete|partial --render-json /app/work/render.json`；再 `python scripts/selftest.py` 验证确定性。
剩余时间用于增量改进（修正连接方向、补图例、重渲染、复核 source_ref/quote），每次改动后更新 change_log。

## 5. 自检清单（逐条可勾选，对应评分维度）
- [ ] 实际效果·完成度：交付可编辑分层 SVG + 真实渲染预览，不是纯文字或单张截图。
- [ ] 实际效果·正确性：部件名称/数量/接口与材料一致；连接方向正确；箭头无反；数值标注与来源一致。
- [ ] 实际效果·完整性：六类字段（entities/relations/data_encoding/legend_annotations/editable_sources/exports）齐全。
- [ ] 实际效果·可核验：SVG 每个图元含 `data-source-ref`；result.json 含 sources（locator+quote）与 checks，可独立复跑。
- [ ] 使用增益：IR→布图→自检流水线可复用，返工与人工修改量下降；效率靠“先落盘骨架、增量改进”保证。
- [ ] 创新增益：沉淀确定性 IR 中间表示与布图/编号/图例规则（见 `references/ir_schema.md`）。
- [ ] 可靠体验：脚本仅标准库、相对路径、异常有降级；同输入两次产物一致（`scripts/selftest.py` 可验）。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。

## 6. 降级策略（缺数据/缺软件时必须如实标注，禁止编造）
- 材料缺失或无法解析：`limitations`/`uncertainty` 标注“无法判断”，不补造部件、数值或来源；`pending_human` 记录待确认项。
- 渲染工具缺失：只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF。
- 字体缺失（中文方框）：用 `font-discovery` 查字体；缺中文字体时在 `exports.fonts` 标注并降级英文标签，不假装嵌入成功。
- 脚本报错：读 stderr 定位；同一策略失败两次即换策略，错误写入 change_log 与 limitations。
- 坐标/接口冲突：标记 uncertainty，优先遵循原始材料原文，不改写科学内容。
- 确定性约定：SVG 与 IR 的“科学内容”逐字节确定；时间戳与运行时计数允许不同（`selftest.py` 用 canonicalize 剔除后比较）。

## 7. 脚本与资源（相对路径，标准库）
- `scripts/main.py`：`init`（骨架）/ `build`（IR→分层 SVG + 尽力渲染）/ `check`（结构自检）/ `finalize`（合并收尾）/ `selftest`。
- `scripts/selftest.py`：确定性 + 契约 + 结构自检 + 空/坏输入降级，四类用例全过才退出码 0。
- `references/ir_schema.md`：IR 字段、图元与确定性布图规范。
- `references/checklist.md`：可勾选检查清单与常见失败样例。

## 8. 最小调用示例（示例输出，非正式案例）
```bash
cd /skills/weekly-skill
python scripts/main.py init --case /input/case.json --work /app/work --out /app/output
# 抽取 IR 写入 /app/work/ir.json 后：
python scripts/main.py build --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/output --render
python scripts/main.py check --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json
python scripts/main.py finalize --work /app/work --out /app/output --status complete
python scripts/selftest.py
```
