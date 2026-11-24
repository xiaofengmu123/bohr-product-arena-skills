---
name: sci-figure-editor
description: 从科研材料（文字、数据表、草图）抽取实体、关系与数据映射，生成可溯源、可继续编辑的分层 SVG 示意图与数据图，并完成 PDF/PNG 导出与期刊参数自检。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 1.1.0
---

# 科研示意图生成与编辑（sci-figure-editor）

## 1. 角色与适用场景

本 Skill 负责「来源材料 → 实体关系清单 → 图面规范 → 可编辑图稿 → 合规导出件」的完整链路。核心判断不看美观，只看四点可检验标准：① 每个实体、关系、数值与单位都能追溯到来源材料的逐字片段；② 图例、配色、标记与数据表分类逐项一致，改数据可重绘；③ 交付物同时含可继续编辑的分层源文件与可打开使用的导出件；④ 来源缺失、描述矛盾、导出失败时如实标注，不编造。

适用：机制图、流程图、结构关系图、概念示意图，以及由已给数据驱动的柱/点图图例重绘。不适用：统计数值计算与统计方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识（`case_id`/`id`）、任务说明、材料文件路径、输出约束相关字段。
- `/input/task.md`：题目总体要求与评分维度。
- 材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定路径，通常在 `/input/` 下。

**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 解析拿到的是原始 PDF，可能没有预抽取文本层；无文本层时用 pdftotext/pdftoppm/OCR，并在 `sources.text_mode` 标注（`native` / `ocr` / `missing`）。工作目录用 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含以下字段（未知值填 `null`/`[]`，不删未知键）：

```json
{
  "case_id": "<从 case.json 提取，缺失填 'unknown'>",
  "schema": "editable_relationship_schematic/v1",
  "status": "complete|partial|failed",
  "editable_source_files": [
    {"path": "figure01.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure01.png", "format": "png", "width_px": 2400, "height_px": 1400,
     "dpi": 300, "color_space": "RGB", "font_check": "embedded|none|replaced", "note": ""}
  ],
  "provenance": [
    {"element": "<SVG 元素 id>", "source_ref": "<材料定位>", "quote": "<逐字原文>",
     "mapping": "<数据/图例映射>", "version": "v1", "confirmed": true}
  ],
  "sources": [{"ref": "<材料引用>", "path": "/input/...", "locator": "<页码/节/行>",
              "text_mode": "native|ocr|missing", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "reason": ""}],
  "errors": [], "degradations": [],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}],
  "runtime": {"started_at": "ISO8601", "finished_at": null,
              "wall_seconds": null, "model_requests_used": null}
}
```

约束：

- **可编辑源文件**：至少一个分层 SVG（含多个命名 `<g>` 图层、可编辑 `<text>`、可替换字体），SVG 图元带 `data-source-ref`/`data-quote` 属性便于溯源。Office/PPT 类图稿必须同时给出真实渲染的 PDF/PNG 预览。
- **导出件**：至少一组可打开使用的 PNG 或 PDF；目标期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查并写入 `export_files` 对应字段。
- **status**：`complete`（全部交付且自检通过）/`partial`（部分交付，须在 `uncertainty`/`pending_human` 标注 limits）/`failed`（未完成并说明原因）。各数组为空时在对应字段说明原因，不得静默省略。
- `runtime.model_requests_used` 由平台记录，Skill 不得编造，脚本只回填可实测的墙钟时间。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。分四阶段，**前 3 分钟内必须先用 `main.py` 落盘一版 `status=partial` 的 result.json 骨架，之后只做增量改进**；任何阶段都不等收尾才写文件。

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 早落盘 | 0–3 min | ≤8 | 运行 `main.py` → result.json(partial) + `/app/work` |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → `entities.json` + `figure_spec.json` + provenance/sources 增量 |
| 2 成图导出 | 7–12 min | ≤20 | `figure_spec.json` → figure.svg → PNG/PDF → `selftest.py` 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 result.json(status) |

具体步骤：

1. **早落盘**：`python scripts/main.py --input /input --output /app/output --work /app/work`。即使读输入失败也要写 `status=failed/partial` 骨架并记入 `errors`。
2. **抽取实体关系**：读实际材料，形成可核对清单（实体/分组/流程/标注、关系方向与类型、数值与单位、图例项），每条带 `locator + quote`。写 `/app/work/entities.json` 与 `/app/work/figure_spec.json`，并把来源写回 result.json 的 `provenance`/`sources`。
3. **生成源文件**：`python scripts/spec_to_svg.py --spec /app/work/figure_spec.json --out /app/output/figure01.svg`。
4. **导出与自检**：用可用工具导出 PNG/PDF；`python scripts/selftest.py --result /app/output/result.json --output-dir /app/output`。
5. **复核收尾**：逐条过 §5 清单，修复实体/方向/图例不一致，补 limits，更新 change_log，置 status。可再次 `python scripts/main.py ... --run` 做增量合并。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance 每条含 locator + 逐字 quote，可独立复核

**使用增益（25）**
- [ ] 数据改动后能重绘（改 `figure_spec.json` 重跑 `spec_to_svg.py`）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

**创新增益（15）**
- [ ] 文字描述→实体关系清单的抽取方法可说明
- [ ] 数据表驱动图例重绘（legend 与 entities/series 同源）
- [ ] 导出前合规检查（`selftest.py`）真实运行

**可靠体验（10）**
- [ ] 按本文件步骤可独立完成一次出图
- [ ] 重复运行稳定产出（确定性布局、固定色板），异常可识别可处置
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 模板/配色/图例规范/导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 6. 降级与诚实标注

- 缺 PDF 文本层 → 用 pdftoppm/OCR，并在 `sources.text_mode` 标 `ocr` 与「需人工复核」。
- 缺字体 → 用 font-discovery 找替代并记录替换；无法嵌入则 `font_check="none"` 并在 uncertainty 说明。
- 渲染/导出失败 → 保留 SVG 源文件，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG。
- 材料缺失或矛盾 → 记入 `uncertainty`/`pending_human`；不确定的实体不画或明确标「推测」，绝不编造基因名/浓度/时间点/单位。
- 所有 limitations 写入 `uncertainty`、`pending_human` 与 `change_log`，不在 result.json 冒充完成，不伪造评测分数与运行结果。

## 7. 脚本与资源（相对路径调用）

- `python scripts/main.py --input /input --output /app/output --work /app/work [--run]`：早落盘骨架 + 增量合并 + 编排。
- `python scripts/spec_to_svg.py --spec <spec.json> --out <file.svg>`：确定性分层 SVG 渲染（标准库）。
- `python scripts/selftest.py --result <result.json> --output-dir <dir> [--selftest]`：契约 + 产物自检。
- 规范：`references/figure-grammar.md`（spec JSON 格式）、`references/contract-checklist.md`（字段定义与评分对照）。

脚本只用 Python 标准库，从 Skill 根目录用相对路径调用，无外部依赖；重复运行结果一致。
