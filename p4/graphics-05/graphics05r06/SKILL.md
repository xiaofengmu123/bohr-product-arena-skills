---
name: graphical-abstract
description: >-
  从官方 /input/case.json 与 task.md 提取实体、关系、数据与版面约束，生成分层可编辑 SVG 与真实
  PNG/PDF 预览；先落盘可复核的中间产物 plan.json / figure_model.json，逐元素溯源（locator + 逐字
  quote），并写出 /app/output/result.json（含 case_id 与 output_contract 全部字段 + status /
  sources / uncertainty / pending_human / change_log）。适用于图形摘要、示意图、机制图、流程图与
  图表编辑任务。
version: 1.1.0
---

# Graphical Abstract Skill（图形摘要）

## 1. 角色与何时使用

你是"科学图稿作者"，为单个固定科研案例产出**单页图形摘要**（研究问题→方法→核心结果→结论），
同时交付**分层可编辑源文件**与**真实渲染预览**，并让图中每个实体、关系、数值、图例都可溯源。
触发条件：`task.md` 要求生成或编辑图形摘要 / 示意图 / 机制图 / 流程图 / 图表时使用本 Skill。
不写论文正文、统计分析或实验设计；只对图中科学内容表达、版式、可编辑性与导出质量负责。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料、output_contract）与 `/input/task.md`。
  材料 PDF/CSV/图片可能位于 `/input/` 或其子目录；**读真实文件，不信任标签**。PDF 文本取自
  PDF 本体（无预抽取答案层）。
- 工作文件放 `/app/work/`，最终交付物放 `/app/output/`。
- Skill 安装于 `/skills/weekly-skill/`。以该目录为 cwd 运行脚本（`cd /skills/weekly-skill`），
  相对路径 `scripts/core_impl.py` 才能解析。

## 3. 输出契约（/app/output/result.json）

必须写入 `/app/output/result.json`，包含：

- `case_id`（取自 case.json）+ `case.json` 的 `output_contract` 中**每个字段**（缺失给
  `null`/`[]`，不改名不删键）；若案例未给出，使用 `references/output_schema.md` 的规范字段。
- 规范字段：`graphical_abstract` {question, method, results, conclusion}；`editable_source`
  （SVG 路径 + layers + text_editable + checksum）；`exports`（PNG/PDF + renderer + status）；
  `entities`、`relations`、`data_mapping`、`legend`；`plan_path`、`figure_model_path`。
- 附加字段：`status`（恰为 `completed | partial | failed`）、`sources`、`uncertainty`、
  `pending_human`、`limitations`、`change_log`、`runtime`。

可编辑源文件为分层 SVG（`fig_<case_id>.svg`）：命名 `layer-*` 分组、文字保持可编辑 `<text>`、
元素带 `data-source`/`data-ref`/`data-channel` 属性。导出件为真实渲染的 PNG/PDF；若无渲染器，
如实记录 `preview_not_rendered`，绝不伪造图片。

## 4. 中间表示与可复核（本版核心变体）

两阶段中间产物把"想画什么"与"怎么画"分离，任何一步都可独立复核、diff 与重跑：

- `plan.json`（语义意图层，Agent 手工可编辑）：panel 角色、实体/关系/数据/图例、版式约束、
  `sources`（每项带 `locator` + 逐字 `quote`）、`uncertainty`、`pending_human`、`change_log`。
- `figure_model.json`（解析几何层，脚本确定性生成）：画布、分层、每个图元的坐标/尺寸/视觉通道/
  源指针/数据指针，以及 plan 的 `checksum`。

相同输入 + 相同 `seed` → 相同 `figure_model.json` → 字节一致 SVG（固定随机种子、id 排序、确定性
布局）。`result.json` 的 `data_mapping` 把每个数据项指回 figure_model 图元与源定位。

## 5. 执行流程（900 s / 64 请求预算）

按顺序执行；每步列出 输入→输出。**规则：前 3 分钟内必须落盘一版合法 `result.json`（status=partial）
骨架，之后只做增量改进。**

| # | 步骤（时间/请求） | 输入 → 输出 |
|-|-|-|
| 1 | 归一化并落盘骨架（≤60 s / ≤6 req） | 读 case.json+task.md → `python scripts/main.py init`；产出 `/app/work/case_normalized.json`、`plan.json` 骨架、`result.json`（partial） |
| 2 | 抽取事实（≤90 s / ≤12 req） | 在 `plan.json` 填入 entities/relations/data/legend/sources（逐字 quote + locator）；读 PDF/CSV |
| 3 | 构建图稿（≤60 s / ≤6 req） | `python scripts/main.py build`；产出 `figure_model.json` + `fig_<case_id>.svg` + 尝试 PNG/PDF |
| 4 | 校验与溯源（≤60 s / ≤8 req） | `python scripts/main.py validate`；补齐 sources/data_mapping/legend，修复严重缺陷 |
| 5 | 增量改进（≤6 min / ≤20 req） | 看校验报告 → 修溢出/小字/低对比/缺源 → 重复 2–4；每次改写 bump `change_log` |
| 6 | 定稿（≤60 s / ≤6 req） | 终版 SVG/PNG/PDF + 报告 → `status`=completed/partial/failed，写 limitations/uncertainty |
| — | 预留（~2 min / ~6 req） | 异常处理、渲染器回退、复核 |

请求上限 64 次。步骤被阻塞（缺文件、实体歧义、导出失败）时，同一策略**重试不超过 2 次**，
如实记录 limitation 并以 partial 交付。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] `result.json` 含 case_id + output_contract 全部字段 + status/sources/uncertainty/pending_human/change_log（结果完整性/可核验性）。
- [ ] 每个框/箭头/条形/图例色块映射到一条 entity/relation/data，各带 sources 指针（实体关系与数据映射正确）。
- [ ] 箭头方向与来源句一致；单位与比例尺标签在图上（结果正确性）。
- [ ] SVG 分层、命名、文字可编辑、无 text-as-path；PNG/PDF 为真实文件（可编辑源文件/导出件）。
- [ ] 无画布溢出、无 <8 px 字号、无低对比配色；灰度/色盲分离已检查（版式与视觉层级）。
- [ ] 相同输入复现相同结构；每次改写 `change_log` 递增（运行稳定性/可复核）。
- [ ] 缺失/歧义项写入 uncertainty/limitations，不编造（异常处理）。
- [ ] 脚本自相对路径 `scripts/*.py`、纯标准库运行；无隐藏个人文件（生态贡献/复用性）。
- [ ] `python scripts/selftest.py` 全部通过（自检脚本）。

## 7. 降级策略（诚实优先）

- **缺数据/数值**：字段置 `null`，写入 `uncertainty`，不估计。
- **实体/方向歧义**：拆分 id 或用无向连接，注明歧义。
- **缺渲染器/字体替换**：保留 SVG 源文件，`exports` 记 `preview_not_rendered`；依次尝试
  cairosvg / rsvg-convert / inkscape / chromium headless，以及平台 pdf-render-tool、office-renderer、
  browser-automation；平台工具渲染出的 PNG/PDF 用 `python scripts/main.py record-exports
  --png <path> --pdf <path> --renderer <name>` 回填（脚本校验魔数后写入）。绝不用假图顶替。
  字体替换写入 `limitations`，可编辑 SVG 仍计作源文件。
- **无 case.json**：init 写 `status=failed` 骨架并退出非 0；报告阻塞，停止后续步骤。
- **坏 JSON/坏编码/超 2MB 材料**：捕获并记入 limitations，继续处理其余材料。

## 8. 证据溯源

每个关键结论/数值/图元必须能指回材料的**逐字片段**：`locator`（如 `materials/paper.pdf#p3`）
+ `quote`。冲突信息两个都记录并标 `source_conflict`。缺失写 `pending_human` 或 `not_reported`。
不虚构引用、DOI、数值。
