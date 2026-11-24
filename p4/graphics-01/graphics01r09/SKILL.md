---
name: sci-figure-studio
description: 从科研材料（文字、数据表、草图）抽取实体、关系与数据映射，生成可溯源、可继续编辑的分层 SVG 示意图，并完成 PNG/PDF 导出与期刊参数自检。用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 1.0.0
---

# sci-figure-studio

## 1. 角色与边界
本 Skill 走通「来源材料 → 可编辑图稿 → 合规导出件」链路：抽取实体/关系/数据映射 → 按图形语法布局 → 分层可编辑 SVG → 导出 PNG/PDF → 逐项自检。正确性不看美观，只看三点可检验标准：每个实体/关系/数值/单位可追溯到来源或金标准；图例/配色/坐标与数据逐项一致且改数据可重绘；同时交付可编辑源文件与合规导出件。
适用：机制图、流程图、结构关系图、概念示意图、图表编辑。不适用：统计数值计算与方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

## 2. 输入契约
运行时原始输入固定为：
- `/input/case.json`：case_id、任务说明、材料路径、可能的 output_contract。
- `/input/task.md`：题目要求与评分维度。
材料文件（论文 PDF、数据表、草图图片）由 case.json 指定，通常在 `/input/` 下。**必须先读实际文件内容**，禁止凭文件名或领域标签补造基因名/浓度/时间点/单位。PDF 无文本层时用 pdftotext/pdftoppm/OCR，并在 sources 标注「OCR 识别，需人工复核」。
工作目录 `/app/work/`（中间产物），最终产物 `/app/output/`（含 result.json）。脚本仅用 Python 标准库，从 Skill 根目录用相对路径调用。

## 3. 输出契约
必须产出 `/app/output/result.json`。若 case.json 含 output_contract，**原样回填其全部键**，再叠加标准字段。标准字段如下：

```json
{
  "case_id": "…",
  "status": "complete|partial|insufficient_evidence|input_error|failed",
  "editable_source_files": [{"path": "figure01.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}],
  "export_files": [{"path": "figure01.png", "format": "png", "width_px": 2400, "height_px": 1400, "dpi": 300, "color_space": "RGB", "font_check": "embedded|none|replaced", "note": ""}],
  "provenance": [{"element": "图元ID", "source_ref": "材料定位", "locator": "页码/节/行列", "quote": "逐字原文", "mapping": "数据/图例映射", "version": "版本", "confirmed": true}],
  "sources": [{"ref": "引用", "path": "/input/…", "locator": "", "quote": "", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "reason": ""}],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}],
  "errors": [], "degradations": [], "limits": [],
  "runtime": {"started_at": "", "finished_at": "", "budget_s": 900, "request_budget": 64, "notes": ""}
}
```

约束：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；至少一个可打开使用的 PNG/PDF 导出件；provenance 每条带 locator + 逐字 quote；空数组须说明原因，不得静默省略；status 如实填写。

## 4. 执行流程与预算
总预算 **900 秒、64 次模型请求**。**前 3 分钟内必须落盘一版 result.json 骨架（status=partial），之后只做增量改进**；任何阶段不等收尾才写文件。

| 阶段 | 时间 | 输入 → 输出 |
|-|-|-|
| 0 骨架 | 0–3 min | `scripts/main.py init` → result.json(partial) + /app/work |
| 1 抽取 | 3–7 min | 读材料 → work/entities.json + work/figure_spec.json，回写 provenance/sources |
| 2 成图导出 | 7–12 min | `scripts/main.py render --result …` → SVG + PNG/PDF |
| 3 复核 | 12–15 min | `scripts/main.py validate` → 修复 → 终版 status |

步骤：
1. 落骨架：`python scripts/main.py init --case /input/case.json --task /input/task.md --input /input --out /app/output --work /app/work`。读失败也先写 status=insufficient_evidence/input_error 骨架。
2. 抽取：读实际材料，形成实体（节点/分组/流程/标注）、关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值单位、图例项；写 work/entities.json 与 work/figure_spec.json，并逐条回写 provenance（locator+quote）与 sources。
3. 成图：`python scripts/main.py render --spec /app/work/figure_spec.json --out /app/output/figure01.svg --png /app/output/figure01.png --result /app/output/result.json`。脚本会尽力调用 rsvg-convert/inkscape/magick/chrome；仍无渲染器时用 `python -c "import cairosvg; cairosvg.svg2png(url='/app/output/figure01.svg', write_to='/app/output/figure01.png', output_width=2400, output_height=1400)"` 或浏览器自动化截图导出 PDF/PNG。全部失败则保留 SVG，export_files 如实记 failed。
4. 自检：`python scripts/main.py validate --result /app/output/result.json --out /app/output`。
5. 收尾：过 §6 清单，修复实体方向/图例不一致，补 limits，更新 change_log，置 status。

## 5. 脚本
从 Skill 根目录相对路径调用，全部标准库：
- `scripts/main.py init|render|validate|all`：入口（`--help` 查看参数）。
- `scripts/figure.py`：确定性 SVG 渲染器 + 尽力 PNG 导出。
- `scripts/selftest.py`：本地自检（空目录/坏 JSON/缺输入/渲染/校验）。
figure_spec.json 语法见 references/figure-spec.md。`init` 与 `all` 幂等：已存在 result.json / figure_spec.json / entities.json 时保留不覆盖，可放心重复运行做增量改进。

## 6. 自检清单（逐条勾选，对应评分维度）
实际效果（40）
- [ ] 走通「材料→图稿→导出」，非套模板插画
- [ ] 实体/关系/方向/层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中无来源不存在的基因名/浓度/时间点/单位
- [ ] 图例/配色/标记与数据表分类数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号
- [ ] provenance 每条有 locator+quote，可独立复核
使用增益（25）
- [ ] 改 spec 重跑 render 可重绘
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改与返工
创新增益（15）
- [ ] 文字→实体关系清单的抽取方法可说明
- [ ] 图例与实体同源（legend 与 entities 共享颜色与映射）
- [ ] validate.py 真实运行并留 validation_report.json
可靠体验（10）
- [ ] 按本文件可独立完成一次出图
- [ ] 重复运行结果稳定（确定性布局、无随机）
- [ ] 缺数据/缺字体/导出失败如实标注，不编造
生态贡献（10）
- [ ] 说明/依赖/路径/边界/维护信息齐全
- [ ] figure-spec 语法、配色、导出预设可复用
- [ ] 沉淀可分享案例与经验（不预写正式答案）

## 7. 降级与诚实标注
- 缺 PDF 文本层 → OCR，sources 标「OCR，需复核」。
- 缺字体 → font-discovery 找替代并记录；无法嵌入则 font_check="none" 写入 uncertainty。
- 渲染/导出失败 → 保留 SVG，export_files 记 failed+原因，status=partial，不伪造 PNG。
- 材料缺失/矛盾 → uncertainty + pending_human；不确定实体不画或标「推测」，绝不编造。
- 空输入 → status=insufficient_evidence；坏 JSON → status=input_error；均写入 errors/degradations。
- 所有 limits 写入 uncertainty/limits/change_log，不得冒充 complete；不伪造评测分数。

## 8. 证据溯源
每个关键图元/数值/结论写回 provenance 或 sources，须含 locator（页码/节/行列）+ 逐字 quote。冲突信息不隐藏：同指标两来源不一致时都记录并标 source_conflict。缺失写 not_reported 或 pending_human，不虚构 DOI/数值。

## 9. 边界与维护
一个 Skill 只解决示意图生成编辑，不做通用大而全。无真实材料时输出「材料不足」而非示例填充。提交前在陌生环境跑 `python scripts/selftest.py`。依赖：Python ≥3.9 标准库；PNG 导出依赖系统可用渲染器（rsvg-convert/inkscape/magick/chrome），缺省降级为仅 SVG + PDF 提示。
