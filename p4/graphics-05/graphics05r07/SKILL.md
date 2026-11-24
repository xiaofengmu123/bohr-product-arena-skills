---
name: graphical-abstract-robust
description: >-
  Deterministic, source-bound, verifiable graphical-abstract authoring for the official
  /input/case.json and /input/task.md. Extracts entities, relations, data and layout
  constraints, persists a partial result.json skeleton within the first 3 minutes, builds
  a layered editable SVG with per-element data-source provenance, renders real PNG/PDF
  previews, validates figure and source mapping, and degrades honestly on empty input,
  malformed JSON, missing arguments and oversized files without crashing. Use for
  graphical-abstract, schematic, mechanism, flowchart and chart-edit tasks.
version: 1.1.0
---

# Graphical Abstract Skill（鲁棒版）

## 1. 角色与适用时机

你是单页科研图形摘要的作者。给定一个固定科研案例，产出一份“研究问题→方法→核心结果→结论”的
单页图形摘要，同时交付可继续编辑的分层源文件与真实渲染预览件，并保证每个图元都能回指来源材料。
本 Skill 适用于 `task.md` 要求制作图形摘要（graphical abstract）、示意图、机制图、流程图或图表编辑
的任务。**本 Skill 不写论文正文、不做统计分析、不设计实验方案**，只对图形中的科学表达、版式、
可编辑性与导出质量负责。本版强化鲁棒性：空输入、坏 JSON、缺参数、超大文件都要优雅降级、不崩溃，
并把缺失如实写入 limitations。

## 2. 输入契约

- 运行时输入固定为 `/input/case.json`（案例事实、材料与输出契约）与 `/input/task.md`（题目文本）。
  材料 PDF/CSV/图片可能一并放在 `/input/` 下；**读取真实文件，不依据文件名或标签猜测内容**。
  PDF 文本来自 PDF 本体（平台不提供预抽取答案层）。
- 工作文件写 `/app/work/`，最终产物与可编辑图稿写 `/app/output/`。
- Skill 安装在 `/skills/weekly-skill/`；脚本一律用相对路径 `scripts/*.py` 调用，
  并从该目录运行（`cd /skills/weekly-skill` 后再执行），保证相对路径可解析。
- 输入发现先行、后假设：不假定固定文件名/键名；`case.json`、`task.md`、`materials/`、CSV、图片均需扫描识别。

## 3. 输出契约

必须写 `/app/output/result.json`，内容包含 `case_id`、题目 `output_contract` 中的**全部键原样回填**
（缺值给 `null`/`[]`，不改名、不删除未知键），并额外追加：`status`、`sources`、`uncertainty`、
`change_log`。题目未给出 `output_contract` 时，使用 `references/output_schema.md` 的规范字段。

规范字段（canonical）：`graphical_abstract`（question/method/results/conclusion）、
`editable_source`（SVG 路径 + 图层 + text_editable）、`exports`（PNG/PDF + renderer + 状态）、
`entities`、`relations`、`data_mapping`、`legend`、`sources`、`uncertainty`、`change_log`、
`limitations`、`pending_human`。`status` 只取 `completed | partial | failed` 三值。

- 可编辑源文件为分层 SVG（`fig_<case_id>.svg`）：具名 `layer-*` 分组，文字保留可编辑 `<text>`，
  图元带 `data-source`、`data-entity`/`data-relation` 属性。
- 导出件为真实渲染的 PNG/PDF 预览；无渲染器时记录 `preview_not_rendered`，**绝不伪造图像**。
- 每个关键结论、数值、图元都带 locator + 逐字 quote；冲突信息两个都记录并标 `source_conflict`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**铁律：前 3 分钟内必须把合法的 result.json 骨架（status=partial）落盘到 `/app/output/`，
之后只做增量改进，任何中途异常都不留下不完整产物。**

| 步骤 | 时间/请求 | 输入 → 输出 |
|-|-|-|
| 1 归一化与骨架落盘 | ≤60s / ≤6 req | 运行 `python scripts/main.py --input /input --output /app/output --work /app/work`；生成 `/app/work/case_normalized.json`、`figure_spec.json` 模板与 result.json 骨架（partial） |
| 2 抽取事实 | ≤90s / ≤12 req | 从归一化文本抽取实体/关系/数据/图面约束，写入 `figure_spec.json`；按需读取 PDF/CSV/图片 |
| 3 构建图稿 | ≤60s / ≤6 req | `python scripts/main.py --build`（或 Agent 编辑 figure_spec 后重跑）；产出 `fig_<case_id>.svg` + `fig_manifest.json` + 尝试 PNG/PDF |
| 4 校验与溯源 | ≤60s / ≤8 req | `python scripts/selftest.py --check` 或 `python scripts/main.py --validate`；补齐 sources/data_mapping/legend，修关键缺陷 |
| 5 增量改进 | ≤6 min / ≤20 req | 检查报告 → 修溢出/小字/低对比/缺来源 → 重跑步骤 3–4 → 每次改写 change_log 递增 |
| 6 终稿 | ≤60s / ≤6 req | 终版 SVG/PNG/PDF + 报告 → 置 status（completed/partial/failed）→ 写 limitations/uncertainty → 终版 result.json |
| — 预留 | ~2 min / ~6 req | 异常处理、渲染器回退、复核 |

模型请求总数不得超过 64 次。任一步骤受阻（文件缺失、实体歧义、导出失败），同一策略重试**两次**仍失败即停止重试，如实记录 limitation，以 `partial` 继续。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] result.json 含 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/change_log（结果完整性 / 可核验性）。
- [ ] 每个方框/箭头/柱/图例色块映射到一个 entity/relation/data 条目，各带 sources 指针（实体关系与数据映射正确）。
- [ ] 箭头方向与来源句一致；数值带单位、比例尺/条件标签齐全（结果正确性）。
- [ ] SVG 分层、具名、文字可编辑、无 text-as-path；PNG/PDF 为真实文件（可编辑源文件 / 导出件）。
- [ ] 无画布溢出、无 <8px 字号、无低对比配色；灰度/色盲分离已检查（版式与视觉层级）。
- [ ] 同一输入重复运行得到相同结构；change_log 随改写递增（运行稳定性 / 可复核）。
- [ ] 缺项/歧义写入 uncertainty/limitations，绝不编造（异常处理）。
- [ ] 脚本仅用标准库、以相对路径 `scripts/*.py` 运行；无隐藏个人文件（生态贡献 / 复用性）。
- [ ] 运行 `python scripts/selftest.py` 通过空输入、坏 JSON、缺参数、超大文件四类用例（鲁棒性）。

## 6. 降级与诚实策略

- **空输入目录**：写合法 `status=failed` 结果，limitations 说明“输入为空、无证据”，pending_human 列出所需材料，退出码 0，不抛异常。
- **坏 JSON / 坏编码**：用 `utf-8-sig` 读取并容错（BOM、坏编码降级）；JSON 解析失败时记录到 `errors`/`degradations`，从 task.md 推导 case_id 继续，其余材料照常处理。
- **缺参数 / 未知参数**：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
- **超大文件**：单文件 >2MB 跳过并记入 degradations，不整体中断。
- **缺数据/缺软件**：字段置 `null`，uncertainty/limitations 标注；无渲染器时保留 SVG 并记 `preview_not_rendered`，再尝试平台 pdf-render-tool / office-renderer / browser-automation；**不伪造 PNG、不编造数据**。
- **写盘失败**：降级到最小合法产物（仅 result.json 的 failed 骨架），不让进程崩溃。
- **实体/方向歧义**：拆 id 或用无向连接，标注 ambiguity。
- **无 case.json**：main.py 写 failed 骨架、记 limitation 后正常退出并上报阻塞，不继续依赖步骤。

## 7. 脚本与调用

```bash
cd /skills/weekly-skill
python scripts/main.py --help                 # 查看参数，退出码 0
python scripts/main.py --input /input --output /app/output --work /app/work   # 完整跑一轮
python scripts/main.py --build --spec /app/work/figure_spec.json               # 仅重构图稿
python scripts/selftest.py                    # 自检（空/坏/缺/超四类用例 + SVG/JSON 合法性）
```

脚本仅依赖 Python 标准库；确定性布局（固定随机种子、固定画布与排序）。证据引用格式：
`locator`（如 `materials/paper.md#p3` 或 `case.json:methods`）+ `quote`（逐字片段）。
