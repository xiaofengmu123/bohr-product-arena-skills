---
name: sci-figure-model
description: 从科研材料（文字、数据表、草图/示意图）抽取实体、关系、数值与图面约束，先沉淀为结构化中间表示 figure_model.json，再由该模型确定性渲染分层可编辑 SVG 与矢量 PDF/PNG 导出件，并逐项校验来源溯源、图例-数据映射与期刊参数。适用于机制图、流程图、结构关系图、概念图与数据驱动图表；不用于统计推断、海报版式、艺术插画或论文正文写作。
version: 1.0.0
---

# 科研示意图生成与编辑（sci-figure-model）

## 1. 角色与何时使用

本 Skill 走通「来源材料 → 结构化中间表示 → 可编辑图稿 → 合规导出」链路。核心不是画得好看，而是三个可检验点：① 每个实体、关系、数值、单位都能追溯到来源材料或金标准；② 图例、配色、坐标与所依据数据逐项一致，改数据后能同步重绘；③ 交付物同时含可继续编辑的源文件与符合目标参数的导出件。

**何时使用**：生成/编辑机制图、流程图、结构关系图、概念图，以及把数据表画成图表。**不用**：统计检验与统计方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

## 2. 核心设计：中间表示 figure_model.json

与“模板插画”的本质区别：**先抽取，再渲染**。所有科学内容先写进 `/app/work/figure_model.json`（字段见 `references/figure-model.md`），渲染脚本只读模型，不接触材料。

- 改一个标签/关系方向/图例颜色 = 改模型对应字段 → 重跑 `render.py`，不重写绘图代码。
- 每个 entity / relation / legend item 可携带 `provenance`（`source_ref` + `locator` + `quote`），图稿自身（SVG `<metadata>`）也内嵌模型摘要，审稿人可独立复核。
- 布局确定性（无随机量）：同一模型两次渲染字节一致。
- 关系语义硬编码：`activation`=实线箭头、`inhibition`=实线 T 端、`containment` 用 `groups[].contains`（不用连线）、`mapping`=虚线、`parallel`=无箭头，杜绝“抑制画成激活、上下游倒置、包含画成并列”。

## 3. 输入契约

运行时原始输入固定：

- `/input/case.json`：案例标识（`case_id`）、任务说明、材料文件路径、输出约束（`output_contract` 相关字段）。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定路径，通常在 `/input/` 下。**必须先读实际文件内容，禁止凭文件名或领域标签脑补基因名、浓度、时间点、单位。** PDF 拿到的是原始 PDF，可能无预抽取文本层；无文本层时用 pdftotext/pdftoppm/OCR，并在 `sources` 标注「OCR 识别，需人工复核」。

工作目录 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。预装工具见 `environment/resources.yaml`；本 Skill 脚本只用 Python 标准库，从 Skill 根目录以相对路径调用。

## 4. 输出契约

必须产出 `/app/output/result.json`。规则：

1. **原样回填 `output_contract` 全部键**：从 `case.json` 读 `output_contract`（或 `outputContract`/`output_spec`），每个键都写进 result.json，缺值给 `null`/`[]`，不改名、不删未知键。
2. 追加标准字段：`case_id`（缺失填 `unknown`）、`status`、主产物、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`、`figure_model`、`plan`。
3. 关键字段示例：

```json
{
  "case_id": "<string>",
  "status": "complete|partial|insufficient_evidence|input_error|failed",
  "editable_source_files": [
    {"path": "figure01.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure01.png", "format": "png", "width_px": 2400, "height_px": 1400,
     "dpi": 300, "color_space": "RGB", "font_check": "embedded|base14|none|replaced", "note": ""}
  ],
  "provenance": [
    {"element": "<entity/relation id>", "source_ref": "<材料定位>", "locator": "<页码/节>",
     "quote": "<逐字引文>", "mapping": "<数据/图例映射>", "version": "<版本>", "confirmed": true}
  ],
  "sources": [{"ref": "<材料引用>", "path": "/input/...", "locator": "<页码/节>", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}]
}
```

约束：可编辑源文件至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体族）；Office/PPT 类图稿必须同时给出真实渲染的 PDF/PNG 预览；`status` 诚实填写；各数组若为空必须在对应字段说明原因，不得静默省略。

## 5. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。分四阶段；**前 3 分钟内必须落盘一版 `status=partial` 的 result.json 骨架，之后只做增量改进，任何阶段都不等收尾才写文件。**

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → `result.json(partial)` + `/app/work/figure_model.json` + `plan.json` |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → 填充 `figure_model.json` + provenance/sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | `figure_model.json` → SVG → PDF/PNG → selftest 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 `result.json(status)` |

具体步骤（脚本均在 Skill 根目录用相对路径调用）：

1. **落骨架**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。读取失败也要先写 `failed/partial` 骨架。
2. **抽取实体关系**：读实际材料，形成可核对清单：实体（节点/分组/流程/标注）、关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值与单位、图例项，逐条写 `provenance` 的 `quote`。写 `/app/work/figure_model.json`，并回写 result.json 的 `provenance/sources`。
3. **生成源文件**：`python scripts/main.py render --model /app/work/figure_model.json --output /app/output [--dpi 300]`，产出 `figure.svg` + `figure.pdf`（有 pdftoppm 时再出 `figure.png`）。
4. **导出与自检**：`python scripts/main.py validate --result /app/output/result.json --model /app/work/figure_model.json --output /app/output --input /input`。
5. **复核收尾**：逐条过 §6 清单，修复实体/方向/图例不一致，补 limits，更新 `change_log`，置 `status`。

## 6. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→模型→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制=T 端、激活=箭头、上下游不倒置、包含=group）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致（分类数=颜色数=标记数）
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] `provenance` 每条能定位到来源（`source_ref`+`locator`+`quote`），可独立复核

**使用增益（25）**
- [ ] 数据改动后能重绘（改 `figure_model.json` 重跑 `render.py`）
- [ ] 输出可编辑 SVG（`<g>` 图层、`<text>` 文字、可替换字体族）
- [ ] `change_log` 如实记录人工修改量与返工轮次减少

**创新增益（15）**
- [ ] 文字描述→实体关系清单的抽取方法可说明（模型字段对应抽取步骤）
- [ ] 数据表驱动图例重绘（legend 与 entities/relations 同源，`maps_to`+`data_mapping`）
- [ ] 导出前合规检查真实运行（`validate`/`selftest.py` 有真实输出）

**可靠体验（10）**
- [ ] 按本文件步骤可独立完成一次出图（`python scripts/main.py selftest` 退出码 0）
- [ ] 重复运行稳定产出（确定性布局，字节一致），异常可识别可处置
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 模型规范/配色/图例规范/导出预设可复用（`references/`）
- [ ] 沉淀可分享案例与经验（不预写正式答案）

## 7. 降级与诚实标注

- 缺 PDF 文本层 → 用 OCR/pdftoppm，并在 `sources` 标注「OCR 识别，需人工复核」。
- 缺字体 → 用 font-discovery 找替代并记录替换；SVG 声明回退字体族；PDF 用 base-14 或标准 CID 字体时 `font_check` 如实填 `base14`/`none`。
- 渲染/导出失败 → 保留 SVG 源文件；`export_files` 记 `failed` 与原因，`status=partial`，不伪造 PNG/PDF。
- 材料缺失或矛盾 → 记入 `uncertainty`；不确定的实体不画或明确标注「推测」，绝不编造基因名/浓度/时间点/单位。
- 所有 limitations 写入 `uncertainty` 与 `change_log`；不伪造评测分数与运行结果。

## 8. 边界

一个 Skill 只解决一类任务：从材料到可编辑科研示意图。不承担统计推断、海报版式、艺术插画、正文写作；没有真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充。
