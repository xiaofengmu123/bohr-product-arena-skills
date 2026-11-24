---
name: sci-figure-authoring
description: 从科研材料（文本、数据表、草图）抽取可溯源的实体—关系清单，写入单一中间表示（FigureIR），据此确定性渲染分层可编辑 SVG，并完成 PNG/PDF 导出与期刊参数自检。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 2.0.0
---

# 科研示意图生成与编辑（sci-figure-authoring）

## 1. 角色与适用场景

本 Skill 负责「来源材料 → 可溯源 FigureIR → 分层可编辑图稿 → 合规导出件」链路。核心不是“画图”，而是**单一数据源（FigureIR）驱动**：实体、关系、图例、标注、证据全部写入 `work/figure_ir.json`，由脚本确定性渲染 SVG，并从同一 IR 派生 `result.json` 的 provenance/sources——图、数据、证据三者同源，改数据重跑脚本即重绘。

适用：机制图（激活/抑制链）、流程图、结构关系图（包含/并列/上下游）、图表的图例与数据映射编辑。不适用：统计数值计算与方法选择、海报封面版式、艺术插画美化、论文正文写作。

判定标准只看三点可检验项：① 每个实体、关系、数值与单位可追溯到材料或金标准（locator + 逐字 quote）；② 图例、配色、坐标与所依据数据逐项一致，改数据可重绘；③ 交付同时含分层可编辑源文件与合规导出件。

## 2. 输入契约（先发现，再假设）

运行时原始输入固定为 `/input/case.json` 与 `/input/task.md`；材料文件（PDF、数据表、草图）由 `case.json` 指定路径，通常在 `/input/` 下。**必须先读实际文件内容**，禁止凭文件名或领域标签脑补基因名、浓度、时间点、单位。PDF 无文本层时用 pdftotext/pdftoppm/OCR，并在 `sources` 标注识别来源。

工作目录 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。脚本只用 Python 标准库，从 Skill 根目录以相对路径调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id`、`status`、主产物与运行元信息：

```json
{
  "case_id": "<case.json 提取，缺失填 unknown>",
  "status": "complete|partial|failed",
  "editable_source_files": [{"path": "figure01.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}],
  "export_files": [{"path": "figure01.png", "format": "png", "width_px": 1600, "height_px": 1100, "dpi": 300, "color_space": "RGB", "font_check": "embedded|none|unknown", "ok": true, "note": ""}],
  "provenance": [{"element": "e1", "source_ref": "paper.pdf", "locator": "p3 左栏", "quote": "…", "mapping": "实体/关系/图例映射", "version": "v1", "confirmed": true}],
  "sources": [{"ref": "paper.pdf", "path": "/input/paper.pdf", "locator": "p1-3", "used_for": "实体关系抽取"}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "why": "", "suggested_action": ""}],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}],
  "runtime": {"tool_version": "2.0.0", "started": "", "finished": ""}
}
```

约束：

- **可编辑源文件**：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；图表另附数据文件。
- **导出件**：至少一组可打开使用的 PNG 或 PDF；尺寸、dpi、字体嵌入、线宽、色彩空间逐项检查写入 `export_files`。
- **provenance**：每条含 `locator` 与逐字 `quote`；无原文引用而由 Agent 推断的，`quote` 填 `""`、`confirmed=false`，并同步写入 `uncertainty` 与 `pending_human`。
- **status**：`complete`（全部交付且自检通过）/ `partial`（部分交付，须在 `uncertainty` 或 `change_log` 标注 limits）/ `failed`（未完成，说明原因）。
- 各数组若为空，必须写原因，不得静默省略。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求、单次回复 ≤16384 tokens**。分四阶段；**前 3 分钟内必须落盘一版 `result.json` 骨架（status=partial），之后只做增量改进**，任何阶段都不等收尾才写文件。

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → result.json(status=partial) + work/ 目录 + figure_ir.template.json |
| 1 抽取建模 | 3–7 min | ≤20 | 实际材料 → work/figure_ir.json（实体/关系/图例/标注 + evidence） |
| 2 校验成图 | 7–12 min | ≤20 | figure_ir.json → 校验 → figure01.svg → PNG/PDF → 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 result.json(status) |

步骤：

1. **落骨架**：`python scripts/main.py --input /input --output /app/output --work /app/work`。首次运行自动生成 result.json 骨架与 IR 模板；读失败也写 `status=partial/failed`。
2. **抽取建模**：读实际材料，形成可核对清单，逐条填写 `work/figure_ir.json`（字段见 `references/figure-ir-schema.md`）：实体（id/label/kind/group/attributes）、关系（type 用受控词，方向 from→to 不可倒置，抑制≠激活）、数值与单位、图例项。每个关键元素带 `evidence{source_ref,locator,quote,confidence}`。
3. **校验成图**：重跑 `scripts/main.py`，脚本按顺序执行 schema 校验 → 证据完整性 → 图例-数据一致性 → 确定性渲染 SVG → 导出与导出合规检查；失败项写入 errors/warnings。
4. **导出自检**：`python scripts/selftest.py`；PNG/PDF 由环境工具完成（见 §7），脚本验证文件头/尺寸。
5. **复核收尾**：逐条过 §5 清单，修复实体/方向/图例不一致，补 limits，更新 change_log，置 status。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**

- [ ] 走通「材料→FigureIR→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance 每条含 locator + 逐字 quote，可独立复核

**使用增益（25）**

- [ ] 改动 figure_ir.json 重跑 main.py 即重绘（数据驱动）
- [ ] 输出分层 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

**创新增益（15）**

- [ ] 文字→实体关系清单的抽取方法可说明（IR 受控词表）
- [ ] 图例与 entities/relations 同源并由脚本交叉校验
- [ ] 渲染前校验 + 导出后合规检查真实运行

**可靠体验（10）**

- [ ] 按本文件步骤可独立完成一次出图
- [ ] 重复运行稳定（确定性布局、固定顺序）
- [ ] 缺数据/缺字体/导出失败如实标注，不编造

**生态贡献（10）**

- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] FigureIR 模板、配色、图例规范、导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 6. 降级与诚实标注

- 缺 PDF 文本层 → OCR/pdftoppm，`sources` 标注「OCR 识别，需人工复核」。
- 缺字体 → font-discovery 找替代并记录替换；无法嵌入则 `font_check="none"` 并在 uncertainty 说明。
- 渲染/导出失败 → 保留 SVG，`export_files` 记 `ok:false` 与原因，`status=partial`，不伪造 PNG/PDF。
- 材料缺失或矛盾 → 记入 `uncertainty`；不确定的实体不画或标「推测」，绝不编造基因名/浓度/时间点/单位。
- 无证据引用 → `quote=""`、`confirmed=false`，同步进 `pending_human`，不得在 result.json 冒充完成。
- 不伪造评测分数、运行结果、DOI 或引用。

## 7. 依赖与环境

- 运行：Python 3.8+，仅标准库；`scripts/main.py`、`scripts/core_impl.py`、`scripts/selftest.py`。
- 导出：脚本依次探测 `rsvg-convert → inkscape → cairosvg → chromium --headless`；探测不到则诚实降级。PDF 可另由 latex-compiler/office-renderer/browser-automation 生成后重跑自检。
- 规范：IR 字段见 `references/figure-ir-schema.md`；校验顺序与 status 判定见 `references/checklist.md`。
- 维护：改渲染逻辑只改 `core_impl.py`；改契约字段同步 `SKILL.md` §3 与两个 references。
