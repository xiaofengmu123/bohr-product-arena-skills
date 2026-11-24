---
name: sci-figure-editor
description: 从科研材料（文字、数据表、草图）抽取实体、关系与数据映射，生成并编辑可溯源、可继续编辑的分层 SVG 示意图，并完成 PDF/PNG 导出与期刊参数自检。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 1.1.0
---

# 科研示意图生成与编辑（sci-figure-editor）

## 1. 角色与何时使用

本 Skill 负责「来源材料 → 可编辑图稿 → 合规导出件」的完整链路：抽取科学实体与关系，按图形语法布局，保证图例/颜色/坐标与数据逐项一致，交付分层可编辑源文件与可打开使用的导出件。适用于机制图、流程图、结构关系图、概念示意图与图表编辑。

不适用：统计图表的数值计算与统计方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。若案例要求这三类任务，应在 `result.json` 中声明「超出本 Skill 边界」并转交。

判对不看美观，只看三个可检验点：① 图中每个实体、关系、数值、单位都能追溯到来源材料或金标准；② 图例、配色、坐标与所依据数据逐项一致，改数据后可重绘；③ 交付物同时含可编辑源文件与合规导出件。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识（`case_id`）、任务说明、材料文件路径、输出约束（`output_contract` 相关字段）。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定路径，通常在 `/input/` 下。**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 解析拿到的是原始 PDF，可能没有预抽取文本层；无文本层时用 `pdftotext`/`pdftoppm`/OCR，并如实标注识别来源。

工作目录用 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。预装工具：pdftotext/pdftoppm、Python（NumPy/Matplotlib）、PDF 渲染、OCR、LaTeX、office-renderer、font-discovery、browser-automation。本 Skill 的脚本只用 Python 标准库，从 Skill 根目录用相对路径调用。

## 3. 输出契约（`/app/output/result.json`）

必须产出 `result.json`，包含题目 `output_contract` 的全部字段，并追加运行元信息：

```json
{
  "case_id": "<从 case.json 提取，缺失时填 'unknown'>",
  "editable_source_files": [
    {"path": "figure01.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure01.png", "format": "png", "width_px": 2400, "height_px": 1400,
     "dpi": 300, "color_space": "RGB", "font_check": "embedded|none|replaced", "note": ""}
  ],
  "provenance": [
    {"element": "<图面元素ID>", "source_ref": "<材料定位 locator>", "quote": "<逐字片段>",
     "mapping": "<数据/图例映射>", "version": "<版本>", "confidence": "confirmed|inferred|missing"}
  ],
  "status": "complete|partial|failed",
  "sources": [{"ref": "<材料引用>", "path": "/input/...", "locator": "<页码/节>", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "reason": "", "action_needed": ""}],
  "errors": [], "degradations": [],
  "runtime": {"started_at": "", "finished_at": "", "budget_s": 900, "budget_requests": 64},
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}]
}
```

约束：

- **可编辑源文件**：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；图表另附数据文件。Office/PPT 类图稿必须同时给出真实渲染的 PDF/PNG 预览。
- **导出件**：至少一组可打开使用的 PNG 或 PDF；目标期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查并写入 `export_files`。
- **status**：`complete`（全部交付且自检通过）/ `partial`（部分交付，须在 `uncertainty` 或 `change_log` 标注 limits）/ `failed`（硬错误：输入不可读、工具链全缺且无法产出任何图稿）。**材料不足不等于 `failed`**：有 SVG 但证据不全时置 `partial`，并把缺口写进 `uncertainty`/`pending_human`。
- 各数组若为空，必须在对应字段中说明原因，不得静默省略。`provenance` 每条必须带非空 `source_ref` 与逐字 `quote`。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**，单响应含推理最多 16384 token。分四阶段，任何阶段都不等收尾才写文件。**前 3 分钟内必须落盘一版 `result.json` 骨架（status=partial），之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → `result.json(status=partial)` + `/app/work` 目录 |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → `entities.json` + `figure_spec.json` + provenance/sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | `figure_spec.json` → `figure01.svg` → PNG/PDF → `selftest.py` 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 `result.json(status)` |

具体步骤：

1. **落骨架**：运行 `python scripts/main.py --input /input --out /app/output --work /app/work`。即使 case.json 读取失败也先写 `status=failed/partial` 骨架，绝不留下空目录。
2. **抽取实体关系**：读实际材料，形成可核对清单：实体（节点/分组/流程/标注）、关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值与单位、图例项。逐条写 `source_ref` + 逐字 `quote`。写 `/app/work/entities.json` 与 `/app/work/figure_spec.json`，并把每条来源回写 `result.json` 的 `provenance/sources`。
3. **生成源文件**：运行 `python scripts/main.py --input /input --out /app/output --work /app/work --spec /app/work/figure_spec.json`（复用步骤 1 的命令，加 `--spec` 即可重渲染）。
4. **导出与自检**：脚本尝试导出 PNG/PDF（纯标准库 PDF 兜底，见 §8），运行 `python scripts/selftest.py --result /app/output/result.json --output-dir /app/output --work /app/work`。
5. **复核收尾**：逐条过 §7 清单，修复实体/方向/图例不一致，补 limits，更新 change_log，置 status。

## 5. 脚本调用（从 Skill 根目录，相对路径）

- `python scripts/main.py --input /input --out /app/output --work /app/work [--spec /app/work/figure_spec.json] [--no-export]`
- `python scripts/selftest.py --result /app/output/result.json --output-dir /app/output --work /app/work`

`main.py` 只做编排（<30KB）；渲染、导出、校验的实现在 `scripts/core_impl.py`。`figure_spec.json` 的 JSON 规范见 `references/figure-grammar.md`；评分维度对照与降级矩阵见 `references/contract-checklist.md`。

## 6. 证据溯源（硬要求）

- 每个关键结论、数值、图元都要指回材料中的**逐字片段**：`provenance[].source_ref` 写 locator（如 `paper.pdf#p3`、`table1.csv#row5`），`provenance[].quote` 写逐字原文，不得转述或补全。
- `sources[].locator` 与 `provenance[].source_ref` 必须能独立定位；PDF 无文本层时用 OCR/pdftoppm，并在 `sources` 标「OCR 识别，需人工复核」。
- 冲突信息不隐藏：同一指标两个来源不一致时两条都记录，标 `source_conflict`，并写入 `pending_human`。
- 不虚构引用、DOI、数值、基因名；缺失写 `not_reported` 或 `pending_human`，不编造。

## 7. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance 每条有 locator + 逐字 quote，可独立复核

**使用增益（25）**
- [ ] 数据改动后能重绘（改 spec 重跑 `main.py --spec`）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次的减少

**创新增益（15）**
- [ ] 文字描述→实体关系清单的抽取方法可说明
- [ ] 数据表驱动图例重绘（legend 与 entities 同源）
- [ ] 导出前合规检查（selftest.py）真实运行并产出报告

**可靠体验（10）**
- [ ] 按本文件步骤可独立完成一次出图
- [ ] 重复运行稳定产出（固定种子、确定性布局、原子写盘、幂等重跑）
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 模板/配色/图例规范/导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 8. 降级与不确定性（本版重点）

本版把「缺什么、为什么、影响多大、如何补救」显式化，机器故障与科学不确定性分开记录，缺数据绝不编造。

| 场景 | 处置 | 落盘字段 |
|---|---|---|
| case.json 缺失/不可读 | 写骨架 `status=failed`，列出缺口 | `errors` + `uncertainty` |
| 材料缺失 | 不画该实体，标 `confidence=missing` | `uncertainty` + `pending_human` |
| 描述矛盾/两来源冲突 | 两条都记录，标 `source_conflict` | `uncertainty` + `pending_human` |
| PDF 无文本层 | OCR/pdftoppm，标注「需人工复核」 | `sources[].locator` + `uncertainty` |
| 无法判定关系方向/类型 | 标 `confidence=inferred`，图面写「推测」 | `provenance[].confidence` + `uncertainty` |
| 缺字体 | font-discovery 找替代；无法嵌入 `font_check="none"` | `export_files[].font_check` + `uncertainty` |
| 缺 PNG 渲染器 | 用纯标准库写矢量 PDF 兜底；PNG 记 failed 与原因 | `export_files` + `degradations` |
| 导出失败 | 保留 SVG 源文件，不伪造 PNG | `export_files`（failed）+ `status=partial` |

- 三态置信度：`confirmed`（逐字 + locator）、`inferred`（可说明推导，图面标「推测」）、`missing`（未画或画占位框）。**只画能追溯到来源的元素。**
- 纯标准库 PDF 兜底：无外部转换器时，脚本仍写一份合法矢量 PDF（Base-14 Helvetica，无嵌入，`font_check="none"`；CJK 字符替换并在 `uncertainty` 说明）。这保证任何环境都至少交付一份可打开的矢量导出件。
- 所有 limitations 写入 `uncertainty` 与 `change_log`，不得在 result.json 冒充完成；不伪造评测分数与运行结果。
