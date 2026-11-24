---
name: sci-figure-workflow
description: 从科研材料（论文、数据表、草图）抽取实体、关系与数据映射，生成并编辑可溯源、可继续编辑的分层 SVG 示意图，导出 PNG/PDF 并按期刊参数自检，将题目评分维度逐条映射到流程与自检项。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化、艺术插画或论文正文写作。
version: 1.0.0
---

# 科研示意图工作流（sci-figure-workflow）

## 1. 角色与适用场景

本 Skill 负责「来源材料 → 可编辑图稿 → 合规导出件」的完整链路：抽取科学实体与关系，按图形语法布局，保证图例/颜色/坐标与数据逐项一致，交付分层可编辑 SVG 源文件与可打开使用的 PNG/PDF 导出件，并给出逐元素溯源记录。

**何时使用**：机制图、流程图、结构关系图、概念示意图的生成与编辑；需要从论文/数据表/草图抽取实体关系并保证图面可核验；需要同时交付可编辑源文件与期刊合规导出件。
**不适用**：统计图表的数值计算与统计方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

核心判定不看美观，只看三点可检验标准：① 每个实体、关系、数值与单位都能追溯到来源材料或金标准；② 图例、配色、坐标与所依据数据逐项一致，改数据后可重绘；③ 交付物同时含可编辑源文件与合规导出件。科学判断由模型原生完成，脚本只做契约执行与自检，不提供「照搬字面即通过」的假通过。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识（`case_id` 或 `id`）、任务说明、材料文件路径、可能的 `output_contract` 字段。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定路径，通常在 `/input/` 下。**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 无预抽取文本层时用 pdftotext/pdftoppm/OCR，并在 `sources` 标注识别来源。输入发现不假设固定文件名，用 `python scripts/main.py discover --input /input` 扫描；单文件超过 2MB 跳过并记录。

工作目录 `/app/work/`（中间产物），最终产物 `/app/output/`（含 `result.json`）。脚本只用 Python 标准库；若当前目录不是 Skill 根目录，先 `cd /skills/weekly-skill`，再以相对路径调用 `python scripts/main.py ...`（脚本内部按自身文件位置解析依赖，不依赖 CWD）。

## 3. 输出契约

必须产出 `/app/output/result.json`，含 `case_id`、`status`，并**原样回填** `case.json` 的 `output_contract` 全部键（缺值给 `null`/`[]`，不改名、不删未知键）。字段语义见 `references/contract.md`，核心结构：

```json
{
  "case_id": "<case.json 的 case_id/id，缺失为 unknown>",
  "status": "complete|partial|insufficient_evidence|failed",
  "editable_source_files": [{"path":"figure01.svg","kind":"svg","layers":true,"editable_text":true,"note":""}],
  "export_files": [{"path":"figure01.png","format":"png","width_px":2400,"height_px":1400,
                    "dpi":300,"color_space":"RGB","font_check":"embedded|none|replaced","note":""}],
  "provenance": [{"element":"<图面元素ID>","source_ref":"<材料定位>","locator":"<页码/节>",
                  "quote":"<逐字摘录>","mapping":"<数据/图例映射>","version":"<版本>","confirmed":true}],
  "sources": [{"ref":"<材料引用>","path":"/input/...","locator":"<页码/节>","used_for":""}],
  "uncertainty": [{"item":"","reason":"","impact":"","mitigation":""}],
  "pending_human": [{"item":"","why":""}],
  "errors": [], "degradations": [],
  "change_log": [{"time":"ISO8601","action":"","detail":""}],
  "runtime": {"started_at":"","finished_at":"","seconds":0,"model_requests_used":0}
}
```

约束：

- **可编辑源文件**：至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；图表另附数据文件；Office/PPT 图稿必须同时给出真实渲染的 PDF/PNG 预览。
- **导出件**：至少一组可打开的 PNG 或 PDF；期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查并写入 `export_files`。
- **status**：`complete`（全部交付且自检通过）/ `partial`（部分交付，limits 写入 `uncertainty`）/ `insufficient_evidence`（材料不足）/ `failed`（失败并说明原因）。
- 各数组若为空，必须在对应字段或 `change_log` 说明原因，不得静默省略。

## 4. 执行流程与预算

总预算 **900 秒、64 次模型请求、单次回复 ≤16384 token**。分四阶段；**前 3 分钟内必须落盘一版 `status=partial` 骨架，之后只做增量改进**，任何阶段都不等收尾才写文件。

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → `scaffold`（result.json=partial）+ /app/work |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → 实体/关系/图例清单 + provenance/sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | 清单 → figure.svg → PNG/PDF → `validate` 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 result.json(status) |

步骤：

1. **落骨架**：`python scripts/main.py scaffold --input /input --output /app/output --work /app/work`。读取失败也先写 honest 骨架（`insufficient_evidence`/`partial`）。
2. **抽取实体关系**：读实际材料，形成可核对清单（实体、关系及方向、数值与单位、图例项），每条写 `provenance`（locator + 逐字 quote）；同一指标来源冲突时两者都记并标 `source_conflict`。
3. **生成源文件**：按 `references/figure-grammar.md` 布局，写 `/app/output/figure01.svg`（分层 `<g>`、可编辑 `<text>`）；可用 `references/svg-layers-template.svg` 作为分层骨架起点，但实体/关系/图例内容必须全部来自材料。
4. **导出与自检**：导出 PNG/PDF；`python scripts/main.py validate --output /app/output` 校验契约、SVG 可解析、PNG/PDF 头、图层与文字元素。
5. **复核收尾**：逐条过 §5 清单，修复实体/方向/图例不一致，补 limits，`python scripts/main.py record --action ... --detail ... --status ...` 追加 change_log 并置 status。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画（任务完成度 15）
- [ ] 每个实体、关系、方向、层级与来源一致；抑制≠激活、上下游不倒置、包含≠并列（正确性 15）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位（正确性 15）
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致（正确性 15）
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素（完整性 5）
- [ ] provenance 每条有 locator + 逐字 quote，可独立复核（可核验性 5）

**使用增益（25）**
- [ ] 数据改动后重绘：改清单/spec 重跑，图例与图面同源更新（质量 15）
- [ ] 输出可编辑 SVG，改标签不整张重画；改数据文件后图同步更新（质量 15）
- [ ] change_log 如实记录人工修改量与返工轮次（效率 5 + 可验证 5）

**创新增益（15）**
- [ ] 文字/表格→实体关系清单的抽取方法可说明（新增能力 5）
- [ ] 数据表驱动图例重绘：legend 与 entities 同源（方法改进 5）
- [ ] 导出前 validate 真实运行并留下报告（创新证据 5）

**可靠体验（10）**
- [ ] 按本文件步骤可独立完成一次出图（调用易用 3）
- [ ] 空输入/坏 JSON/坏编码/缺文件时降级不崩溃；重复运行结果一致（稳定性 4）
- [ ] 缺数据/缺字体/导出失败如实标注，不编造（异常处理 3）

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全（文档 3）
- [ ] 模板/配色/图例规范/导出预设可复用（可复用 4）
- [ ] 沉淀可分享案例与经验，不预写正式答案（分享 3）

提交前运行 `python scripts/selftest.py`，全部 PASS 再交付。

## 6. 降级与诚实标注

- 缺 PDF 文本层 → OCR/pdftoppm，`sources` 标注「OCR 识别，需人工复核」。
- 缺字体 → font-discovery 找替代并记录替换；无法嵌入则 `font_check="none"` 并在 `uncertainty` 说明。
- 渲染/导出失败 → 保留 SVG 源文件，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG。
- 材料缺失/矛盾/不确定 → 记入 `uncertainty` 或 `pending_human`；不确定实体不画或标注「推测」，绝不编造基因名/浓度/时间点/单位。
- 所有 limitations 写入 `uncertainty` 与 `change_log`；不伪造评测分数、运行结果或软件状态。
- 如需 GitHub 附加分，在提交目录根放 `GITHUB_URL.txt`（本 Skill 不含该文件，由提交者填写）。

## 7. 最小调用示例（示意，非正式案例）

示意输入 `/input/case.json`（仅为说明接口，不冒充正式案例结果）：

```json
{"case_id":"demo-001","task":"绘制 A 促进 B、C 抑制 B 的机制图",
 "output_contract":{"editable_source_files":[],"export_files":[]},
 "materials":["notes.md"]}
```

调用（从 Skill 根目录，脚本只用标准库）：

```bash
python scripts/main.py scaffold --input /input --output /app/output --work /app/work
python scripts/main.py discover --input /input
# Agent 读材料抽取实体关系，写 figure01.svg 与 provenance 后：
python scripts/main.py validate --output /app/output
python scripts/main.py record --output /app/output/result.json --action "finalize" --status complete --detail "…"
```

正式案例由平台固定提供，本示例仅说明接口与完整使用流程。
