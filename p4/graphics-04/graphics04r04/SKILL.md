---
name: scix-evidence-figure-skill
description: 从科研材料（装置清单、装配说明、论文方法段落、工艺/流程描述、研究内容与阶段划分）抽取“实体—连接—步骤—流向”结构化中间表示（IR），以逐字证据（locator+quote）与置信度绑定每个图元，据此生成分层可编辑 SVG 并导出 PNG/PDF 预览，完成自检后输出可复核的 /app/output/result.json。适用于需要“画得对、可编辑、可复跑、可核验”的实验装置与技术路线绘图任务；本版优先保证缺数据不编造、不确定项显式标注。
version: 1.1.0
---

# scix-evidence-figure-skill：证据绑定的科研绘图

## 1. 角色与何时使用

本 Skill 先构建“实体—连接—步骤—流向”结构化中间表示（IR），再用**逐字证据（locator+quote）与置信度**绑定每个图元，据此布图、生成分层可编辑 SVG、导出 PNG/PDF 预览、自检并落盘 result.json。它针对“画得像但不对”：部件名对不上、管路凭空补齐、箭头反向、编号错位、图例与图形不一致、只交付截图。

- **适用**：装置图、机制图、流程图、技术路线图；输入含装置清单、装配说明、论文方法段落、工艺/流程描述、研究阶段划分。
- **不适用**：数据分析与数值拟合；判断实验方案是否科学合理；期刊排版与投稿格式适配。

## 2. 输入契约（运行时）

- 原始输入：`/input/case.json`、`/input/task.md`；科研材料按 `case.json` 指引读取（`/input/materials/` 或指引路径）。
- 先发现、后假设：扫描输入目录，不假设固定文件名/键名；PDF 用平台 `pdf-text-tool`/`pdf-render-tool`/`ocr-tool` 读实际文件，不依据标签假设内容。
- 工作目录 `/app/work`；最终产物写 `/app/output/`（含 `result.json`）。
- 可用工具：`scientific-python`、`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`latex-compiler`、`office-renderer`、`browser-automation`、`font-discovery`。
- 本 Skill 脚本位于 `scripts/`，运行前 `cd /skills/weekly-skill`，用相对路径调用，如 `python scripts/main.py init --case-id <id>`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8）。若 `case.json` 声明了 `output_contract`，按其字段名**原样回填全部字段**，再补下列通用字段；未声明时采用默认契约。

- `case_id`：取自 `/input/case.json`。
- `status`：`in_progress | complete | partial | insufficient_evidence | input_error | failed`（诚实降级，不许伪造）。
- 六类要素（评分维度）：`entities`、`relations`、`data_encoding`、`legend_annotations`、`editable_sources`、`exports`。
- 证据与不确定性：`sources`、`uncertainty`、`pending_human`、`limitations`、`degradations`、`errors`。
- 过程记录：`change_log`（时间戳+动作+状态）、`checks`（自检结果）、`runtime`、`artifacts`。

关键约定：可编辑源文件优先交付**分层 SVG**（layer-relations / layer-entities / layer-annotations / layer-legend），并交付真实渲染的 PNG/PDF 预览；SVG 中每个 entity/relation/annotation/legend 项带 `data-source-ref` 与 `data-confidence` 属性，可追溯回输入材料。

## 4. 证据溯源与不确定性（本版核心设计）

本版相对通用绘图 Skill 的关键取舍：**不把“画出来”当作完成，而把“每个图元可被追责”当作完成。**

- 每个 entity/relation/data_encoding/annotation 必须带 `evidence`：`{locator, quote, status}`；`status ∈ {verbatim, paraphrase, inferred, missing, conflicting, not_reported}`。
- 每个图元带 `confidence ∈ {high, medium, low}`；`confidence=low` 或 `status=missing` 的图元**不得写成既定事实**，须进 `uncertainty`。
- `locator` 形如 `materials/paper.md#p3` 或 `materials/fig3.pdf#page2`；`quote` 为材料逐字原文（无原文则不填）。
- 同一指标两处冲突时，两条都记录并标 `source_conflict`，不隐藏。
- 无法判断的部件、数值、来源一律标 `missing`/`not_reported` 并进 `pending_human`，**禁止补造**。

## 5. 执行流程（总预算 900 秒 / 64 次模型请求，单次回复 ≤16384 tokens）

**硬性要求**：前 3 分钟（约 180 秒）内必须落盘一版合法 `result.json` 骨架（`status=in_progress`，含 `case_id`、初始契约字段、首条 `change_log`），之后只做增量改进；任何时刻均可交付诚实的部分结果。

1. **读输入（≤90s）**：读 `/input/case.json` + `task.md` → 确定案例目标、图种 `kind`、材料路径、`output_contract` 字段。输出 `/app/work/input_summary.json`。
2. **提取 IR（≤150s）**：抽取 entities/relations/steps/stages，逐条附 `evidence` 与 `confidence` → 写 `/app/work/ir.json`（字段见 `references/ir_schema.md`）；不确定项先记 `uncertainty`，不补造。
3. **落盘骨架（≤60s，累计 <180s）**：`python scripts/main.py init --case-id <id> --title "<标题>" --kind <kind>` → 生成 `/app/output/result.json`（in_progress）。
4. **布图生成 SVG（≤180s）**：`python scripts/main.py build --ir /app/work/ir.json --svg /app/work/figure.svg`；坐标缺失时脚本用确定性网格布局并在 metadata 标 `auto-layout`；需微调坐标则改 IR 后重跑。
5. **渲染预览（≤120s）**：用 `browser-automation`/`office-renderer` 把 SVG 导出 PNG/PDF；失败则 `exports.render_status="skipped"`，写入 `limitations`，不伪造成功。
6. **自检（≤120s）**：`python scripts/main.py check --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json`；critical 项必须修订，最多 2 轮。
7. **收尾（≤120s）**：复制产物到 `/app/output/`，`python scripts/main.py finalize --status complete|partial|insufficient_evidence` 写状态、`checks`、`artifacts` 与结束时间。

剩余时间做增量改进（修正连接方向、补图例、重渲染、补 source_ref），每次改动后更新 `change_log`。

## 6. 自检清单（逐条对应评分维度）

- [ ] 完成度：交付分层可编辑 SVG + 真实渲染 PNG/PDF，非纯文字或单张截图。
- [ ] 正确性：部件名称/数量/接口与材料一致；连接方向正确；箭头无反；数值标注与来源一致。
- [ ] 完整性：entities / relations / data_encoding / legend_annotations / editable_sources / exports 六类齐全。
- [ ] 可核验：SVG 含 `data-source-ref`；result.json 含 `sources`、`checks`、`uncertainty`，可独立复跑。
- [ ] 证据：每个关键图元有 `locator` + 逐字 `quote`（缺失时已标 `missing`，不编造）。
- [ ] 使用增益：IR→布图→自检流水线可复用，返工与人工修改量下降。
- [ ] 创新增益：证据绑定 IR、确定性布局、编号/图例规则沉淀于 `references/ir_schema.md`。
- [ ] 可靠体验：脚本仅标准库、相对路径、异常有降级；重复运行结果一致。
- [ ] 生态贡献：说明、输入约定、图元规范、限制与失败清单齐全。

## 7. 降级策略（缺数据/缺软件如实标注，禁止编造）

| 场景 | 处置 |
|---|---|
| 材料缺失/无法解析 | `status=insufficient_evidence` 或 `partial`；`uncertainty`+`pending_human` 标“无法判断” |
| 某部件/数值缺失 | 该图元 `evidence.status=missing`，不写入图形或写入并显式标虚框“?” |
| 渲染工具缺失/失败 | 只交付 SVG，`exports.render_status="skipped"`，`limitations` 说明无 PNG/PDF |
| 字体缺失（中文方框） | `font-discovery` 查字体；缺中文字体时 `exports.fonts` 标注并降级英文标签 |
| 脚本报错 | 读 stderr；同一策略失败两次即换策略，错误写 `change_log`+`limitations` |
| 坐标/接口冲突 | 标 `uncertainty`，优先遵循材料原文，不改写科学内容 |
| 空输入目录 | 退出码 0，`status=insufficient_evidence`，不抛异常 |

## 8. 脚本与资源（相对路径，纯标准库）

- `scripts/main.py`：`init` / `build` / `check` / `finalize` / `run` 子命令，编排流程。
- `scripts/core_impl.py`：IR 规范化、确定性布图、SVG 生成、自检、result 管理。
- `scripts/selftest.py`：包级自检（空输入、坏 JSON、SVG 可解析、断头/缺证据检出、重复运行一致）。
- `references/ir_schema.md`：IR 字段、证据/置信度模型、图元与布图规范。
- `references/checklist.md`：可勾选检查清单与常见失败样例。

## 9. 最小调用示例（示意输出须标明为示例）

```bash
cd /skills/weekly-skill
python scripts/main.py init --case-id demo-1 --title "示例：某反应装置" --kind apparatus
# Agent 抽取证据后编辑 /app/work/ir.json
python scripts/main.py build --ir /app/work/ir.json --svg /app/work/figure.svg
python scripts/main.py check --ir /app/work/ir.json --svg /app/work/figure.svg --out /app/work/checks.json
python scripts/main.py finalize --status partial
```

`python scripts/main.py run --input ./demo/input --output ./demo/output --work ./demo/work` 提供端到端自测（产出均为示例，非正式案例答案）。
