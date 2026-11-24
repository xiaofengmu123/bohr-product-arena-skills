---
name: graphical-abstract
description: >-
  Evidence-bound scientific graphical abstract authoring. Extract entities, relations, data and
  layout constraints with verbatim locator+quote evidence, build a layered editable SVG through
  scripts/main.py and scripts/build_svg.py, render real PNG/PDF previews, validate coverage and
  honesty via scripts/selftest.py, and write /app/output/result.json with case_id plus every
  output_contract field, status, sources, uncertainty, pending_human, limitations and change_log.
  Degradation-first: missing values stay null and are recorded, never fabricated. Use for
  graphical-abstract, schematic, mechanism, flowchart and chart-edit tasks.
version: 2.0.0
---

# Graphical Abstract Skill（证据约束版）

## 1. 角色与何时使用

你是一名科研图稿作者。给定一个固定科研案例，你产出**单页图形摘要**（研究问题→方法→核心结果→结论），同时交付**可编辑分层源文件**与**真实渲染预览**。每个实体、关系、数值和图例项都必须能追溯到材料中的**逐字片段**（locator + quote）。

适用：`task.md` 要求制作图形摘要、示意图、机制图、流程图或图表编辑。不适用：论文正文、统计分析、实验方案设计。

本版的核心取舍是**降级与不确定性显式化**：证据不足时宁可留 `null`、写 `pending_human`，也绝不补造数值或结论。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料、`output_contract`）与 `/input/task.md`；材料 PDF/CSV/图件也可能在 `/input/` 下。**读实际文件，不信标签**；PDF 正文来自 PDF 本身（无预提取答案层），用平台 pdf-text-tool 或 `pdftotext` 解析。
- 工作文件写 `/app/work/`；最终产物写 `/app/output/`（含 `result.json`）。
- Skill 安装在 `/skills/weekly-skill/`。脚本从该目录运行，相对路径 `scripts/*.py` 才可解析（`cd /skills/weekly-skill` 后调用）。

## 3. 输出契约

必须写 `/app/output/result.json`，含 `case_id` **加题目 `output_contract` 的全部字段**（缺值给 `null`/`[]`，不改名、不删未知键；若题目未给，用 `references/contract.md` 的规范 schema），**再加** `status`、`sources`、`uncertainty`、`pending_human`、`limitations`、`change_log`。

规范字段：`graphical_abstract` {question, method, results, conclusion}、`editable_source`（SVG 路径 + layers + text_editable）、`exports`（PNG/PDF + renderer + status）、`entities`、`relations`、`data_mapping`、`legend`、`sources`、`uncertainty`、`pending_human`、`limitations`、`change_log`。

- `status` 仅取 `completed | partial | failed`（见 §7 降级阶梯）。
- 每条 `entities`/`relations`/`data_mapping`/`legend` 必须带 `evidence`：`{locator, quote}`，quote 是材料中的**逐字片段**；无证据则该项不画或标 `pending_human`。
- `editable_source` 是分层 SVG `fig_<case_id>.svg`：命名 `layer-*` 组、文字保持可编辑 `<text>`、元素带 `data-source`。导出件是真实渲染的 PNG/PDF；无渲染器时记 `preview_not_rendered`，**绝不伪造图片**。

## 4. 证据与诚实规则（本版重点）

1. 每个关键结论/数值/图元都写 `locator`（`materials/xx.md#L12` 或 `materials/xx.pdf#p3`）+ 逐字 `quote`。
2. 自动抽取只能给 `confidence=low`；经你核对原文后才可升为 `high`，并保留 locator。
3. 冲突信息不隐藏：同一指标两来源不一致时都记录并标 `source_conflict`，图中取其一并在 `uncertainty` 说明。
4. 材料缺失 → 字段 `null` + `uncertainty`/`pending_human` 条目；材料不充分 → `status=partial` 或 `insufficient_evidence` 语义；**不估计、不编造引用/DOI/数值**。
5. 图面只呈现有证据的要素：`scripts/build_svg.py` 只绘制带 `evidence`（locator+quote）的实体/关系/数据，无证据项留空并由 `scripts/main.py` 校验告警，而不是保留。

## 5. 执行流程（900 s / 64 请求预算）

**硬规则：前 3 分钟内必须落盘一版 `result.json` 骨架（status=partial），之后只做增量改进。**

| # | 步骤（时间/请求） | 输入 → 输出 |
|-|-|-|
| 1 | **落盘骨架**（≤60 s，≤4 req） | 读 `/input` → `python3 -B scripts/main.py --input /input --output /app/output --work /app/work`；脚本先写 `result.json` 骨架 + `evidence_bank.json` + `figure_spec.json` 模板 |
| 2 | **证据抽取与核对**（≤90 s，≤12 req） | 骨架 → 用平台 PDF/OCR 工具读材料，在 `figure_spec.json` 中确认实体/关系/数据/约束，为每条补 locator+quote；脚本的自动候选只作起点 |
| 3 | **建图**（≤60 s，≤6 req） | `figure_spec.json` → `python3 -B scripts/build_svg.py --work /app/work --output /app/output`；得 `fig_<case_id>.svg` + 尝试 PNG/PDF |
| 4 | **校验与溯源**（≤60 s，≤8 req） | `python3 -B scripts/main.py ... --validate` 或 `python3 -B scripts/selftest.py`；补齐 sources/data_mapping/legend，修复字体<8px、溢出、低对比、缺证据 |
| 5 | **增量改进**（≤6 min，≤20 req） | 看报告 → 改 `figure_spec.json` → 重跑 3–4；每次改写 `change_log` 追加一条 |
| 6 | **定稿**（≤60 s，≤6 req） | 终版 SVG/PNG/PDF + 报告 → 定 `status`，写 `limitations`/`uncertainty`/`pending_human`，写终版 `result.json` |
| — | 预留（~2 min，~6 req） | 错误处理、渲染器回退、复跑校验 |

请求上限 64 次；任一步骤被同一策略连续失败 2 次即停止重试，如实记录限制并转 `partial`。脚本只依赖标准库，可从相对路径运行。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] `result.json` 含 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/limitations/change_log（结果完整性/可核验性）。
- [ ] 每个框/箭头/柱/图例色块映射到一条 entity/relation/data 记录，每条带 locator+逐字 quote（实体关系与数据映射正确）。
- [ ] 箭头方向与来源句一致；单位、比例尺、条件标签在图上（结果正确性）。
- [ ] SVG 分层命名、文字可编辑、无 text-as-path；PNG/PDF 为真实文件或 `preview_not_rendered`（可编辑源/导出件）。
- [ ] 无画布溢出、无 <8px 字号、无低对比；灰度/色盲区分已检查（版式与视觉层级）。
- [ ] 同一输入复现相同结构；`change_log` 每次改写递增（运行稳定性/可复核）。
- [ ] 缺失/歧义项在 `uncertainty`/`pending_human`/`limitations`，未编造（异常处理）。
- [ ] 运行 `python3 -B scripts/selftest.py`（空目录、坏 JSON、合成小案例）通过；脚本为 stdlib、相对路径、无个人隐藏文件（生态贡献/复用性）。
- [ ] `limitations` 显式列出：缺数据、字体替换、导出失败、PDF 未解析等（降级与不确定性）。

## 7. 降级阶梯（诚实状态）

- `completed`：四要素齐全、每个关键图元有证据、SVG+PNG/PDF 均真实产出。
- `partial`：主产物或导出件之一可交付，但存在缺证据项/渲染失败/歧义，已在 `limitations`+`uncertainty` 说明。
- `failed`：无 `case.json` 或材料全缺；脚本写 `status=failed` 骨架并退出 1，报告阻塞后停止依赖步骤。

缺失处置：数值缺失→`null`+`uncertainty`；方向歧义→拆分 id 或无向连线并注明；渲染器缺失→保留 SVG + `preview_not_rendered`，尝试平台 office-renderer / pdf-render-tool / browser-automation；字体替换/导出失败→写 `limitations`；材料无 `case.json`→`failed` 骨架。

## 8. 脚本与路径

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --input /input --output /app/output --work /app/work   # 落盘骨架+全流程
python3 -B scripts/build_svg.py --work /app/work --output /app/output            # 由 figure_spec.json 建图
python3 -B scripts/selftest.py                                                   # 自检（合成案例+空目录+坏 JSON）
```

- `scripts/main.py`：发现输入、容错解析、立即写 partial 骨架、自动证据候选、由 `figure_spec.json` 生成规范 `result.json`、校验。agent 只编辑 `figure_spec.json`，不直接改 SVG，保证可复现。
- `scripts/build_svg.py`：确定性布局，把 spec 渲染为分层 SVG，探测可用渲染器产出 PNG/PDF。
- `scripts/selftest.py`：用内置合成案例端到端跑一遍，校验 schema/SVG/导出/空输入/坏 JSON。
- 规范详见 `references/contract.md`（字段与证据格式）与 `references/degradation_rules.md`（降级与不确定性细则）。
