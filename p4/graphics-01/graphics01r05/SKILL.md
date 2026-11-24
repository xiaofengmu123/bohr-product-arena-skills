---
name: sci-figure-contract
description: 从科研材料（文字、数据表、草图）抽取实体、关系与数据映射，生成并编辑可溯源、可继续编辑的分层 SVG 示意图，导出 PNG/PDF 并做期刊参数自检；以「输出契约优先」为核心：先落盘字段完整的 partial 结果骨架，再增量完善并逐字段校验 /app/output/result.json（含 change_log、pending_human、逐字 quote 溯源）。适用于机制图、流程图、结构关系图、概念示意图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 1.0.0
---

# 科研示意图编辑：契约优先（sci-figure-contract）

## 1. 角色与何时使用

本 Skill 负责「来源材料 → 可编辑图稿 → 合规导出件」的完整链路：抽取科学实体与关系，按图形语法布局，保证图例/颜色/坐标与数据逐项一致，交付分层可编辑源文件与可打开使用的导出件。适用：机制图、流程图、结构关系图、概念示意图、图表编辑。不适用：统计数值计算与方法选择、海报封面版式、艺术插画美化、论文正文与文献综述。

判断是否做对，只看三点可检验标准：① 图中每个实体、关系、数值与单位都能追溯到来源材料或金标准；② 图例、配色、坐标与所依据数据逐项一致，改数据后可重绘；③ 交付物同时含可编辑源文件与符合期刊/模板参数的导出件。

## 2. 输入契约

- `/input/case.json`：案例标识（case_id）、任务说明、材料文件路径、输出约束（`output_contract` 相关字段，可能缺失）。
- `/input/task.md`：题目总体要求与评分维度。
- 材料文件（论文 PDF、数据表、草图图片）由 `case.json` 指定路径，通常在 `/input/` 下。**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点或单位。** PDF 解析拿到的是原始 PDF，可能没有预抽取文本层；无文本层时用 pdftotext/pdftoppm/OCR，并如实标注识别来源。
- 工作目录用 `/app/work/`（中间产物），最终产物写 `/app/output/`（含 `result.json`）。脚本只用 Python 标准库，从 Skill 根目录用相对路径调用。

## 3. 输出契约（本版侧重点：契约完整）

必须产出 `/app/output/result.json`，包含 `case_id` + 题目 `output_contract` 的全部字段，并追加 `status / sources / uncertainty / change_log`，另有 `pending_human / degradations / errors / runtime`。字段以 `scripts/scaffold.py` 的 `RESULT_CONTRACT` 与 `references/contract.md` 为**单源**（两处同步，`selftest.py` 校验）。

```json
{
  "case_id": "<从 case.json 提取，缺失填 unknown>",
  "status": "complete|partial|failed|insufficient_evidence|input_error",
  "editable_source_files": [{"path": "", "kind": "svg|data|office", "layers": true, "editable_text": true, "note": ""}],
  "export_files": [{"path": "", "format": "png|pdf", "width_px": 0, "height_px": 0, "dpi": 0,
                    "color_space": "RGB", "font_check": "embedded|none|replaced", "note": ""}],
  "provenance": [{"element": "", "source_ref": "", "quote": "", "mapping": "", "version": "", "confirmed": true}],
  "sources": [{"ref": "", "path": "", "locator": "", "used_for": ""}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [{"item": "", "reason": "", "suggested_action": ""}],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}],
  "degradations": [{"item": "", "reason": "", "fallback": ""}],
  "errors": [{"item": "", "reason": "", "handling": ""}],
  "runtime": {}
}
```

硬约束：可编辑源文件至少一个分层 SVG（`<g>` 图层、可编辑 `<text>`、可替换字体）；图表另附数据文件。导出件至少一组可打开使用的 PNG/PDF，期刊参数（尺寸、dpi、字体嵌入、线宽、色彩空间）逐项检查写入 `export_files`。`provenance` 每条必须含 locator + 逐字 `quote`；数组为空时必须写清原因，不得静默省略。`case.json` 声明的未知 `output_contract` 键一律原样回填（缺值 `null`/`[]`），不改名、不删键。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**；任何阶段都不等收尾才写文件。**前 3 分钟内必须落盘一版字段完整的 partial 结果骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → result.json(status=partial) + /app/work |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → entities.json + figure_spec.json + provenance/sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | figure_spec.json → figure.svg → PNG/PDF → 自检 |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → finalize + 置 status |

具体步骤：

1. **落骨架**：`python scripts/scaffold.py --input-dir /input --output-dir /app/output --work-dir /app/work`。读取失败也要先写 `status=input_error/partial` 骨架。
2. **抽取实体关系**：读实际材料，形成可核对清单：实体（节点/分组/流程/标注）、关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值与单位、图例项。写 `/app/work/entities.json` 与 `/app/work/figure_spec.json`，并把每条来源写回 `provenance/sources`（含 locator + 逐字 quote）。
3. **生成源文件**：按 `figure_spec.json` 生成 `/app/output/figure.svg`（分层、可编辑文本），图表附数据文件。
4. **导出与自检**：用可用工具导出 PNG/PDF；运行 `python scripts/finalize.py --result /app/output/result.json --work-dir /app/work --output-dir /app/output` 做契约校验。
5. **复核收尾**：逐条过 `references/checklist.md`，修复实体/方向/图例不一致，补 limits，更新 change_log，置 status。

## 5. 脚本（相对路径调用）

- `python scripts/scaffold.py --input-dir /input --output-dir /app/output --work-dir /app/work`：按契约生成 result.json 骨架，扫描输入、提取 case_id、回填 output_contract。
- `python scripts/finalize.py --result /app/output/result.json --work-dir /app/work --output-dir /app/output`：合并 `work/*.json` 增量、校验契约、幂等追加 change_log。
- `python scripts/selftest.py`：包级结构自检（frontmatter、契约一致性、脚本可运行、合成往返测试），退出码 0 表示通过。

## 6. 自检清单（逐条勾选，对应评分维度）

见 `references/checklist.md`，摘要如下：

**实际效果（40）**：走通材料→图稿→导出；实体/关系/方向/层级与来源一致；无来源外基因名/浓度/时间点/单位；图例/配色/标记与数据逐项一致；provenance 每条可独立复核（locator + 逐字 quote）。

**使用增益（25）**：改数据可重绘（图例与数据同源）；SVG 可编辑、改标签不整张重画；change_log 如实记录修改量与返工轮次。

**创新增益（15）**：文字→实体关系清单的抽取方法可说明；数据表驱动图例重绘；finalize.py 契约校验真实运行。

**可靠体验（10）**：按本文件可独立完成一次出图；重复运行稳定产出（确定性布局、幂等 finalize）；缺数据/缺字体/导出失败如实标注。

**生态贡献（10）**：说明/依赖/路径/边界齐全；契约、配色/图例规范、导出预设可复用；沉淀案例与经验（不预写正式答案）。

## 7. 降级与诚实标注

- 缺 PDF 文本层 → 用 OCR/pdftoppm，并在 `sources`/`degradations` 标注「OCR 识别，需人工复核」。
- 缺字体 → 用 font-discovery 找替代并记录替换；无法嵌入则 `font_check="none"` 并写入 `uncertainty`。
- 渲染/导出失败 → 保留 SVG 源文件，`export_files` 记 failed 与原因，`status=partial`，不伪造 PNG/PDF。
- 材料缺失或矛盾 → 记入 `uncertainty`/`pending_human`；不确定的实体不画或明确标注「推测」，绝不编造基因名/浓度/时间点/单位。
- 所有 limitations 写入 `uncertainty` 与 `change_log`，不得在 result.json 冒充完成；不伪造评测分数与运行结果。

## 8. 边界与维护

单 Skill 只解决一类任务；无真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充。脚本零第三方依赖；改契约须同步 `scaffold.py` 与 `references/contract.md`，交付前运行 `python scripts/selftest.py`。
