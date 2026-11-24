---
name: sci-figure-editor
description: 从科研材料（论文 PDF、数据表、草图）抽取实体、关系与数据映射，按确定性图形语法渲染分层可编辑 SVG，导出 PDF/PNG 并做期刊参数自检与确定性校验。保证同输入两次产物科学内容一致、图例与数据同源、每个图元可溯源（locator + 逐字 quote）。用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 1.1.0
---

# 科研示意图生成与编辑（sci-figure-editor）

## 1. 角色与适用场景

本 Skill 走通「来源材料 → 实体/关系/图例抽取 → 可编辑 SVG → 合规导出」完整链路。适用：机制图、流程图、结构关系图、概念示意图与图表编辑。不适用：统计数值计算与统计方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

判断做对与否看三个可检验点：① 每个实体、关系、数值、单位都能追溯到材料（locator + 逐字 quote）；② 图例、配色、坐标与所依据数据逐项一致，改数据后重跑即重绘；③ 交付物同时含可继续编辑的分层源文件与符合目标期刊参数的导出件。本版额外承诺**确定性**：同输入两次运行，科学内容核心哈希一致、SVG 字节一致（见 §7）。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识 `case_id`、任务说明、材料文件路径、输出约束字段。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图图片）路径由 `case.json` 指定，通常在 `/input/` 下。**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 解析拿到的是原始 PDF，可能无预抽取文本层；无文本层时用 pdftotext/pdftoppm/OCR，并在 `sources` 标注「OCR 识别，需人工复核」。

工作目录 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。预装工具：pdftotext/pdftoppm、Python（NumPy/Matplotlib）、PDF 渲染、OCR、LaTeX、office-renderer、font-discovery、browser-automation。本 Skill 脚本只用 Python 标准库。

## 3. 输出契约

必须产出 `/app/output/result.json`，含题目 output_contract 的全部字段（若 case.json 另有键，**原样回填**，缺值给 `null`/`[]`，不改名不删键），并追加：

```json
{
  "case_id": "从 case.json 提取，缺失填 'unknown'",
  "status": "complete|partial|failed|input_error|insufficient_evidence",
  "editable_source_files": [
    {"path": "figure01.svg", "kind": "svg", "layers": true,
     "editable_text": true, "font_replaceable": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure01.png", "format": "png", "width_px": 2400,
     "height_px": 1400, "dpi": 300, "color_space": "RGB",
     "font_check": "embedded|none|replaced", "note": ""}
  ],
  "provenance": [
    {"element": "A", "source_ref": "paper.pdf", "locator": "第3页",
     "quote": "逐字片段", "mapping": "图例 L1", "version": "1.0", "confirmed": true}
  ],
  "sources": [{"ref": "", "path": "/input/...", "locator": "", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "why": "", "action": ""}],
  "change_log": [{"step": "", "time": "ISO8601", "action": "", "detail": ""}],
  "errors": [], "degradations": [], "runtime": {}
}
```

- 可编辑源文件：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）。Office/PPT 图稿须同时给真实渲染的 PDF/PNG 预览。
- 导出件：至少一组可打开的 PNG 或 PDF；目标期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查并写入 `export_files`。
- `status`：`complete`（全部交付且自检通过）/`partial`（部分交付，须在 `uncertainty`/`change_log` 标 limits）/`failed`/`insufficient_evidence`/`input_error`。
- 各数组为空必须在对应字段说明原因，不得静默省略。字段定义见 `references/contract-checklist.md`。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。分四阶段，任何阶段都不等收尾才写文件。**前 3 分钟内必须先落盘一版 `result.json`（status=partial）骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json+task.md → result.json(status=partial) + /app/work |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → entities.json + figure_spec.json + provenance/sources 增量 |
| 2 成图导出 | 7–12 min | ≤20 | figure_spec.json → SVG → PNG/PDF → selftest.py 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物+清单 → 修复 → 终版 result.json(status) |

具体步骤：

1. **落骨架**：`python scripts/main.py --stage init`。读取失败也要先写 `status=partial/failed` 骨架。
2. **抽取实体关系**：读实际材料，写 `/app/work/entities.json`（每项带 locator + 逐字 quote）与 `/app/work/figure_spec.json`（见 `references/figure-grammar.md`）。
3. **渲染源文件**：`python scripts/main.py --stage render`（校验 spec → 渲染 SVG → 合并 provenance/sources）。
4. **导出与自检**：用可用工具（cairosvg/rsvg-convert/inkscape/office-renderer）导出 PNG/PDF；`python scripts/main.py --stage finalize` 自动检测导出件并运行 `selftest.py`。
5. **复核收尾**：逐条过 §6 清单，修复实体/方向/图例不一致，补 limits，更新 change_log，置 status。

## 5. 脚本调用（相对路径，仅标准库）

- `python scripts/main.py [--stage init|render|finalize|all] [--case /input/case.json] [--out /app/output] [--work /app/work]`
- `python scripts/spec_to_svg.py --spec /app/work/figure_spec.json --out /app/output/figure01.svg`
- `python scripts/selftest.py --output-dir /app/output --work-dir /app/work [--input-dir /input]`

最小调用示例（示意输出，非正式案例结果）：

```bash
python scripts/main.py --stage init
# 编辑 /app/work/entities.json 与 /app/work/figure_spec.json 后：
python scripts/main.py --stage render
python scripts/main.py --stage finalize
# 输出：/app/output/result.json + figure01.svg + figure01.png/pdf
```

## 6. 自检清单（交付前逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance 每条都有 locator + 逐字 quote，可独立复核

**使用增益（25）**
- [ ] 改 spec 重跑 spec_to_svg.py 即可重绘（数据驱动）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

**创新增益（15）**
- [ ] 文字→实体关系清单的抽取方法可说明
- [ ] 图例与 entities 同源（legend/data 由同一 spec 驱动）
- [ ] selftest.py 导出前合规检查真实运行且退出码为 0

**可靠体验（10）**
- [ ] 按本文件可独立完成一次出图
- [ ] 重复运行稳定产出，异常可识别可处置
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 调色板/图例规范/导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

## 7. 确定性保证（本版重点）

- `spec_to_svg.py` 无随机数、无墙钟时间、无环境字体差异：同 `figure_spec.json` 字节 → 同 SVG 字节。
- `selftest.py` 对 `result.json` 的**科学内容核心**（case_id、status、主产物、provenance、sources、uncertainty、pending_human）取 SHA-256 并与 `/input` 材料指纹一并写入 `/app/work/.determinism.json`；同输入重跑若核心哈希变化，报告 `determinism_violation` 退出码 1。
- 墙钟时间、模型请求数等运行元信息只进 `runtime`/`change_log`，不计入内容哈希（诚实记录，但天然每次不同）。
- 自动布局是纯函数：分组按 id 字典序，组内按规范顺序纵向堆叠，间距固定。

## 8. 降级与诚实标注

- 缺 PDF 文本层 → OCR/pdftoppm，`sources` 标「OCR 识别，需人工复核」。
- 缺字体 → font-discovery 找替代并记录替换；无法嵌入 `font_check="none"` 并写入 uncertainty。
- 渲染/导出失败 → 保留 SVG，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG。
- 材料缺失/矛盾 → 记入 `uncertainty`；不确定实体不画或标「推测」，绝不编造基因名/浓度/时间点/单位。
- 坏 JSON/坏编码/空输入 → 捕获写入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- 所有 limitations 写入 `uncertainty` 与 `change_log`，不在 result.json 冒充完成；不伪造评测分数与运行结果。

## 9. 边界

一个 Skill 只解决「科研示意图生成与编辑」这一类任务；没有真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充；交付前在不属于作者的环境跑一遍 `selftest.py`。
