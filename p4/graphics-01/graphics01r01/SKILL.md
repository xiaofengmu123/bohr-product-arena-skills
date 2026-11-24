---
name: sci-figure-editor
description: 从科研材料（文字、数据表、草图）抽取实体、关系与数据映射，生成并编辑可溯源的分层 SVG 示意图，并完成 PDF/PNG 导出与期刊参数自检。核心强化点是证据溯源：每条科学结论都带 locator 与逐字 quote，图元与证据通过 data-evidence-id 互指。适用于机制图、流程图、结构关系图与图表编辑；不用于统计数值计算、海报美化或论文正文写作。
version: 2.0.0
---

# 科研示意图生成与编辑（sci-figure-editor）

## 1. 角色与适用场景

负责「来源材料 → 可编辑图稿 → 合规导出件」的完整链路：抽取科学实体与关系，
按图形语法布局，保证图例/颜色/坐标与数据逐项一致，交付分层可编辑源文件与
可打开使用的导出件。适用：机制图、流程图、结构关系图、概念示意图、图表编辑。

不适用：统计数值计算与统计方法选择、海报封面版式、艺术插画美化、论文正文与
文献综述。判定成功不看美观，只看三点可检验标准：① 图中每个实体、关系、数值
与单位都能追溯到材料或金标准；② 图例、配色、坐标与所依据数据逐项一致，改数据
后可重绘；③ 交付物同时含可编辑源文件与合规导出件。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识、任务说明、材料文件路径、输出约束。
- `/input/task.md`：题目总体要求与评分维度。

材料文件（论文 PDF、数据表、草图）由 case.json 指定路径，通常在 `/input/` 下。
**必须先读实际文件内容，禁止仅凭文件名或领域标签脑补实体、基因名、浓度、时间点
或单位。** PDF 解析拿到的是原始 PDF，可能没有文本层；无文本层时用
pdftoppm/OCR，并如实标注识别来源。工作目录 `/app/work/`，最终产物写
`/app/output/`（含 result.json）。

## 3. 输出契约

必须产出 `/app/output/result.json`：包含题目 output_contract 全部字段
（`case_id`、`editable_source_files`、`export_files`、`provenance`、`status`、
`sources`、`uncertainty`、`change_log`），并增补本包的溯源强化字段：

```json
{
  "case_id": "<缺失填 unknown>",
  "status": "complete|partial|failed",
  "primary_outputs": [{"path": "figure.svg", "kind": "svg"}],
  "editable_source_files": [
    {"path": "figure.svg", "kind": "svg", "layers": true, "editable_text": true, "note": ""}
  ],
  "export_files": [
    {"path": "figure.png", "format": "png", "width_px": 1600, "height_px": 1000,
     "dpi": 300, "color_space": "RGB", "font_check": "rasterized", "tool": "rsvg-convert",
     "note": ""}
  ],
  "provenance": [
    {"element": "n1", "kind": "entity", "evidence_id": "ev_001",
     "source_ref": "paper.pdf", "mapping": "node label=...", "version": "v1",
     "confirmed": true}
  ],
  "evidence_map": [
    {"id": "ev_001", "claim_kind": "entity", "claim": "...", "source_ref": "paper.pdf",
     "locator": {"page": 3, "anchor": "para 2"}, "quote": "逐字原文",
     "status": "confirmed", "basis": "text", "maps_to": ["n1"], "notes": ""}
  ],
  "sources": [{"ref": "paper.pdf", "path": "/input/paper.pdf", "locator": "p3",
               "used_for": "机制关系"}],
  "uncertainty": [{"item": "", "reason": "", "impact": "", "mitigation": ""}],
  "pending_human": [], "errors": [], "degradations": [],
  "change_log": [{"time": "ISO8601", "action": "", "detail": ""}],
  "runtime": {"started_at": "ISO8601", "case_keys": []}
}
```

字段语义与图面语法见 `references/figure-grammar.md` 与
`references/contract-checklist.md`。各数组为空时必须在对应字段或
`uncertainty` 中说明原因，不得静默省略。

## 4. 执行流程与时间预算

总预算 **900 秒、64 次模型请求**。分四阶段；任何阶段都不等收尾才写文件。
**前 3 分钟内必须先落盘一版 `result.json`（status=partial），之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–3 min | ≤8 | case.json + task.md → result.json(partial) + /app/work |
| 1 抽取 | 3–7 min | ≤20 | 实际材料 → evidence_map.json + figure_spec.json + sources/uncertainty 增量 |
| 2 成图导出 | 7–12 min | ≤20 | figure_spec.json → figure.svg → PNG/PDF → validate |
| 3 复核收尾 | 12–15 min | ≤16 | 产物 + 清单 → 修复 → 终版 result.json(status) |

具体步骤（脚本均从 Skill 根目录用相对路径调用）：

1. **落骨架**：`python scripts/main.py scaffold --input /input --out /app/output --work /app/work`。
   读取失败也要先写 `status=partial/failed` 骨架。
2. **抽取实体关系**：`python scripts/main.py extract ...` 发现材料并抽取 PDF 文本，
   写 `/app/work/extraction_report.json`。随后读实际文本，形成可核对清单：实体、
   关系（激活/抑制/包含/并列/上下游/因果/时序/映射）、数值与单位、图例项。每条
   写 `/app/work/evidence_map.json`（locator + 逐字 quote）与
   `/app/work/figure_spec.json`，并把来源写回 result.json 的 provenance/sources。
3. **生成源文件**：`python scripts/main.py render --spec /app/work/figure_spec.json --svg /app/output/figure.svg`。
4. **导出与自检**：`python scripts/main.py export --svg ...`；再
   `python scripts/main.py validate --result /app/output/result.json --output-dir /app/output`。
5. **合并收尾**：`python scripts/main.py merge --result ... --evidence /app/work/evidence_map.json --spec /app/work/figure_spec.json`，
   逐条过 §6 清单，修复实体/方向/图例不一致，补 limits，置 status。

## 5. 证据溯源规范（本包侧重点）

每个关键结论/数值/图元都必须能指回材料逐字片段：

- `evidence_map` 每条必须含 `locator`（结构化定位：page/section/anchor）与
  `quote`（逐字原文）；缺任一者，validate 直接报错。
- `status` 五态：`confirmed`（quote 支持 claim）、`inferred`（合理推断，notes 写推理）、
  `pending_human`（需人工）、`conflict`（两来源不一致，都记录）、
  `not_reported`（材料未记载，用于显式负向核验「无浓度/无时间点」）。
- 渲染器把 `evidence_id`/`source_ref` 写成 SVG 的 `data-evidence-id`/
  `data-source-ref`，图元与证据可机器复核；`basis="ocr"` 时 sources 标「OCR，需人工复核」。
- 冲突信息不隐藏；不虚构引用、DOI、数值；缺失写 pending_human 或 not_reported。

## 6. 自检清单（逐条勾选，对应评分维度）

**实际效果（40）**
- [ ] 走通「材料→图稿→导出」，不是套模板插画
- [ ] 每个实体、关系、方向、层级与来源一致（抑制≠激活、上下游不倒置、包含≠并列）
- [ ] 图中不出现材料中不存在的基因名/浓度/时间点/单位
- [ ] 图例项、配色、标记与数据表分类和数值逐项一致
- [ ] 覆盖实体、关系、图例、标注、单位、子图编号等必要图面元素
- [ ] provenance/evidence_map 每条都能定位到来源，可独立复核

**使用增益（25）**
- [ ] 数据改动后能重绘（改 spec 重跑 render）
- [ ] 输出可编辑 SVG，改标签不整张重画
- [ ] change_log 如实记录人工修改量与返工轮次

**创新增益（15）**
- [ ] 文字描述→实体关系清单的抽取方法可说明（§5）
- [ ] 数据表驱动图例重绘（legend 与 entities 同源）
- [ ] 导出前合规检查（validate）真实运行

**可靠体验（10）**
- [ ] 按本文件步骤可独立完成一次出图
- [ ] 重复运行稳定产出，异常可识别可处置
- [ ] 缺数据/缺字体/导出失败时如实标注，不编造

**生态贡献（10）**
- [ ] 说明、依赖、路径、边界与维护信息齐全
- [ ] 模板/配色/图例规范/导出预设可复用
- [ ] 沉淀可分享的案例与经验（不预写正式答案）

交付前运行 `python -B scripts/selftest.py`（退出码 0 为通过）。

## 7. 降级与诚实标注

- 缺 PDF 文本层 → 用 OCR/pdftoppm，并在 sources 标注「OCR 识别，需人工复核」。
- 缺字体 → 用 font-discovery 找替代并记录替换；无法嵌入则 font_check="none" 并写 uncertainty。
- 渲染/导出失败 → 保留 SVG 源文件，export_files 记 failed+reason，status=partial，不伪造 PNG/PDF。
- 材料缺失或矛盾 → 写 uncertainty；不确定的实体不画或标「推测」，绝不编造基因名/浓度/时间点/单位。
- 所有 limitations 写入 uncertainty 与 change_log，不得在 result.json 冒充完成；不伪造评测分数与运行结果。

## 8. 脚本

从 Skill 根目录调用，全部只用 Python 标准库（外部渲染器经 subprocess 调用）：

- `python scripts/main.py scaffold --input /input --out /app/output --work /app/work`
- `python scripts/main.py extract --input /input --out /app/output --work /app/work`
- `python scripts/main.py render --spec <spec.json> --svg <out.svg>`
- `python scripts/main.py export --svg <in.svg> --out <dir> --work <dir>`
- `python scripts/main.py validate --result <result.json> --output-dir <dir>`
- `python scripts/main.py merge --result <result.json> --evidence <evidence.json> --spec <spec.json> --work <dir>`
- `python scripts/selftest.py`

实现拆分：`main.py` 为薄调度层（<30KB），`core_impl.py` 承载渲染/导出/校验/证据合并。
