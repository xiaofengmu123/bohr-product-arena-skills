---
name: sci-schematic-editor
description: 从科研材料（文字、数据表、草图）抽取科学实体、关系与数据映射，生成并编辑可溯源、可继续编辑的分层 SVG 示意图，导出并自检 PDF/PNG，交付可独立复核的 result.json。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。本版强化鲁棒性：空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级、不崩溃。
version: 1.1.0
---

# 科研示意图生成与编辑（sci-schematic-editor）

## 1. 角色与何时使用

本 Skill 负责「来源材料 → 可编辑图稿 → 合规导出件」链路：从论文文字、数据表、草图中抽取科学实体与关系，按图形语法布局，生成分层可编辑 SVG，导出 PDF/PNG 并做期刊参数自检，交付带证据溯源与诚实状态的 `result.json`。适用于机制图、流程图、结构关系图、概念示意图、图表编辑。不适用于统计数值计算与统计方法选择、海报/封面版式、艺术插画美化、论文正文与文献综述。

判定不看美观，看三点可检验事实：① 图中每个实体、关系、数值与单位都能追溯回材料原文（locator + 逐字 quote）；② 图例、配色、坐标与所依据数据逐项一致，改 spec 可重绘；③ 交付物同时含可编辑源文件与可打开的导出件。

## 2. 输入契约

运行时原始输入固定为：
- `/input/case.json`：案例标识 `case_id`、任务说明、材料文件路径、`output_contract`（若存在）。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定，通常在 `/input/` 下。**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 拿到的是原始 PDF，可能无预抽取文本层；无文本层时用 pdftotext/pdftoppm/OCR，并在 `sources` 标注「OCR 识别，需人工复核」。

工作目录 `/app/work/`（中间产物：`discovery.json`、`entities.json`、`figure_spec.json`、`validation_report.json`），最终产物写 `/app/output/`（`result.json` 与全部交付件）。脚本只用 Python 标准库，从 Skill 根目录用相对路径调用。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id` 与题目 `output_contract` 的全部字段（原样回填，缺值给 `null`/`[]`，不改名不删未知键），并追加：

```json
{
  "case_id": "…",
  "status": "complete|partial|failed",
  "editable_source_files": [{"path":"…","kind":"svg","layers":true,"editable_text":true,"note":"…"}],
  "export_files": [{"path":"…","format":"png|pdf","width_px":0,"height_px":0,"dpi":0,"color_space":"RGB","font_check":"embedded|none|replaced","note":"…"}],
  "provenance": [{"element":"图元ID","source_ref":"材料定位","quote":"逐字原文","mapping":"数据/图例映射","version":"…","confirmed":true}],
  "sources": [{"ref":"材料引用","path":"/input/…","locator":"页码/节/行","kind":"pdf|table|image|text","ocr":false,"used_for":"…"}],
  "uncertainty": [{"item":"","reason":"","impact":"","mitigation":""}],
  "pending_human": [{"item":"","why":"","suggested_action":""}],
  "errors": [], "degradations": [],
  "change_log": [{"time":"ISO8601","action":"","detail":""}],
  "runtime": {"started_iso":"","ended_iso":"","elapsed_s":0,"model_requests_used":null}
}
```

约束：
- **可编辑源文件**：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；图表另附数据文件（CSV/JSON）。
- **导出件**：至少一组可打开使用的 PNG 或 PDF；目标期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查并写入 `export_files`。
- **status**：`complete`（全部交付且自检通过）/ `partial`（部分交付，limits 写入 `uncertainty`/`change_log`）/ `failed`（未完成，原因写入 `errors`）。材料不足→`partial`+`uncertainty`；输入不可读→`failed`+`errors`。
- 各数组若为空，必须在对应字段或 `uncertainty` 中说明原因，不得静默省略。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**（单响应含推理 ≤16384 token）。分四阶段；**前 3 分钟内必须落盘一版 `result.json`(status=partial) 骨架，之后只做增量改进**，任何阶段都不等收尾才写文件。

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json+task.md → `result.json`(partial)+`work/discovery.json` |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → `work/entities.json`+`work/figure_spec.json`+provenance/sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | `figure_spec.json` → `figure.svg` → PNG/PDF → validate 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物+报告 → 修复 → 终版 `result.json`(status) |

具体步骤（脚本从 Skill 根目录相对路径调用）：
1. **落骨架**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。读取失败也先写 `status=failed/partial` 骨架，绝不空手退出。
2. **抽取实体关系**：读实际材料，形成可核对清单：实体（节点/分组/流程/标注）、关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值与单位、图例项；写 `work/entities.json` 与 `work/figure_spec.json`，每条来源回写 `provenance`/`sources`（含 locator + 逐字 quote）。
3. **生成源文件**：`python scripts/main.py build --spec work/figure_spec.json --output /app/output --work /app/work`。
4. **导出与自检**：用可用工具（pdftoppm/cairosvg/browser/office-renderer）导出 PNG/PDF；`python scripts/main.py validate --result /app/output/result.json --output-dir /app/output --work /app/work`。
5. **复核收尾**：逐条过 §5 清单，修复实体/方向/图例不一致，补 limits，更新 `change_log`，置最终 `status`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] 每条 provenance 都有 locator + 逐字 quote，可独立复核

**使用增益（25）**
- [ ] 改数据后能重绘（改 `figure_spec.json` 重跑 `main.py build`）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] `change_log` 如实记录人工修改量与返工轮次

**创新增益（15）**
- [ ] 文字描述→实体关系清单的抽取方法可说明
- [ ] 数据表驱动图例重绘（legend 与 entities 同源）
- [ ] 导出前合规检查 `main.py validate` 真实运行

**可靠体验（10）**
- [ ] 空输入/坏 JSON/坏编码/缺参数/超大文件均不崩溃、如实标注
- [ ] 重复运行同一输入结果一致（确定性布局、固定排序）
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 模板/配色/图例规范/导出预设可复用
- [ ] 沉淀可分享案例与经验（不预写正式答案）

## 6. 降级策略与诚实标注

- 空输入目录 → `partial` + uncertainty「materials」，退出码 0，不抛异常。
- 坏 JSON / 坏编码 → 容错读取（utf-8-sig→utf-8→latin-1→replace），记录 `errors`/`degradations`，继续处理其余材料。
- 缺参数/未知参数 → 默认 `/input`、`/app/output`、`/app/work`，未知参数忽略。
- 单文件 >2MB 或总量超限 → 截断或跳过，`sources`/`degradations` 记录。
- 缺 PDF 文本层 → OCR/pdftoppm，`sources` 标注「OCR 识别，需人工复核」。
- 缺字体 → font-discovery 找替代并记录替换；无法嵌入则 `font_check="none"` 并在 uncertainty 说明。
- 渲染/导出失败 → 保留 SVG 源文件，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG。
- 材料缺失或矛盾 → 记入 `uncertainty`；不确定的实体不画或明确标注「推测」，绝不编造基因名/浓度/时间点/单位。
- 冲突信息不隐藏：同一指标两处不一致，两处都记录并标 `source_conflict`。

## 7. 脚本与自检

- `python scripts/main.py scaffold --input /input --output /app/output --work /app/work` — 落 partial 骨架。
- `python scripts/main.py build --spec work/figure_spec.json --output /app/output --work /app/work` — spec→分层 SVG。
- `python scripts/main.py validate --result /app/output/result.json --output-dir /app/output --work /app/work` — 契约+产物合规检查。
- `python scripts/main.py run --input /input --output /app/output --work /app/work` — scaffold→build(若有 spec)→validate 一键链。
- `python scripts/selftest.py` — 空输入/坏 JSON/缺输入/坏编码/超大文件/正常 spec/校验诚实/确定性 八类自检。

spec 格式见 `references/figure-grammar.md`；字段定义与降级矩阵见 `references/contract-checklist.md`。
