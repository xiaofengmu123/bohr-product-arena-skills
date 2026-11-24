---
name: graphical-abstract
description: >-
  Produce verifiable, editable, reproducible single-page scientific graphical abstracts from
  /input/case.json and /input/task.md. Extracts entities, relations, data and layout
  constraints, renders a deterministic layered editable SVG (scripts/main.py), attempts real
  PNG/PDF previews, validates figure + source mapping + output-contract alignment
  (scripts/validate via main.py), and writes /app/output/result.json with case_id, every
  output_contract field echoed verbatim (unknown keys null-filled, never dropped), status,
  sources, uncertainty, pending_human, limitations and a monotonically increasing change_log.
  A partial result skeleton is persisted within the first 3 minutes and only improved
  incrementally after. Use for graphical-abstract, schematic, mechanism, flowchart and
  chart-edit tasks.
version: 1.0.0
---

# Graphical Abstract Skill（图形摘要）

## 1. 角色与何时使用

你是单页科研图形作者。给定一个固定科研案例，产出 **单页图形摘要**（研究问题→方法→核心
结果→结论）+ **可继续编辑的分层源文件** + **真实渲染预览件**，且每个实体、关系、数值、
图例都能逐字回到来源材料。本 Skill 用于 `task.md` 要求 graphical abstract、schematic、
mechanism diagram、flowchart 或 chart edit 的任务；**不做**论文正文、统计分析或实验设计。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料清单、可能携带 `output_contract`）与
  `/input/task.md`（任务文本）。材料 PDF/CSV/图片也在 `/input/` 下；**必须读真实文件**，
  不得相信标签或任何预抽取答案层。
- 工作目录 `/app/work/`；最终产物与可编辑件放在 `/app/output/`（含 `result.json`）。
- Skill 安装于 `/skills/weekly-skill/`；脚本用相对路径 `scripts/*.py` 调用，运行时
  `cd /skills/weekly-skill` 或以其为 cwd 执行。
- 输入容错：`case.json` 按 `utf-8-sig → utf-8 → gb18030 → latin-1` 读取并容错 BOM/坏编码/
  坏 JSON；单文件 >2MB 跳过并记入 `degradations`，绝不崩溃。

## 3. 输出契约（本 Skill 的强化方向）

必须写 `/app/output/result.json`，包含：

1. `case_id` + `status`（严格三选一：`completed | partial | failed`）；
2. 案例 `output_contract` 的**全部字段逐一回填**：未知键保留、不改名、不删除；缺值给
   `null`（列表型给 `[]`）。若案例未给 `output_contract`，用 canonical schema
   （见 `references/output_schema.md`）；
3. 追加 `sources` / `uncertainty` / `pending_human` / `limitations` / `errors` /
   `degradations` / `runtime` / `change_log`。

canonical 字段：`graphical_abstract`{question,method,results,conclusion}、`editable_source`
（SVG 路径+layers+text_editable）、`exports`（PNG/PDF 含 renderer+status）、`entities`、
`relations`、`data_mapping`、`legend`、`sources`、`uncertainty`、`limitations`、
`pending_human`、`change_log`。字段类型与逐项含义见 `references/output_schema.md`。

**逐字证据溯源（硬要求）**：`entities/relations/data_mapping/legend` 的每个条目都带
`source:{locator, quote}`；`quote` 是材料中的逐字片段，`locator` 形如 `文件#page=N` 或
`文件#段落`。找不到逐字依据的数值/箭头方向/颜色映射**不得上图**，改入 `uncertainty`。

**change_log**：每次实质改写（骨架→填事实→重绘→导出→定稿）追加一条 `{version, time_utc,
what, why}`，`version` 从 1 单调递增。这是"结果可复核、重复运行一致"的落地证据。

## 4. 执行流程（总预算 900 s / 64 次模型请求；单响应含推理 16384 token）

**铁律：前 3 分钟内必须先落盘一版 `status=partial` 的 `result.json` 骨架**，之后只做增量
改进；任何中途失败都仍有一份合法产物在盘。

| # | 步骤（时间，请求） | 输入 → 输出 |
|-|-|-|
| 1 | **归一化并落盘骨架**（≤60 s，≤6 req） | `/input/case.json`+`task.md` → `python scripts/extract_case.py --input /input --output /app/output --work /app/work`；产出 `/app/work/case_normalized.json`、`/app/work/figure_spec.json`、`result.json`（partial，含全部 contract 字段） |
| 2 | **抽取事实**（≤90 s，≤12 req） | 归一化案例 → 把 entities/relations/data/图面约束写进 `/app/work/figure_spec.json`；用平台 PDF/OCR 工具读材料，每条带 locator+quote |
| 3 | **构建图稿**（≤60 s，≤6 req） | `figure_spec.json` → `python scripts/main.py build`；产出 `fig_<case_id>.svg` + `fig_manifest.json`，再 `python scripts/main.py export` 尝试真实 PNG/PDF |
| 4 | **校验与溯源**（≤60 s，≤8 req） | `python scripts/main.py validate`；补 `sources/data_mapping/legend` 使每个图元可溯源；修 critical 缺陷 |
| 5 | **增量改进**（≤6 min，≤20 req） | 看 `validation_report.json` → 修溢出/小字号/低对比/缺来源 → 重跑步骤 3–4 → 每次改写 bump `change_log` |
| 6 | **定稿**（≤60 s，≤6 req） | `python scripts/main.py finalize` 对齐 contract、追加 change_log、置 `status`；写 `limitations/uncertainty` |
| — | 预留（约 2 min，6 req） | 错误处理、渲染器回退、复检 |

请求上限 64 次；某步被阻塞（缺文件、实体歧义、导出失败）时，**同一策略失败两次即停止重
试**，如实写 limitation，以 `partial` 继续。

## 5. 自检清单（逐条可勾选；对应评分维度）

完整清单见 `references/checklist.md`；`python scripts/selftest.py` 自动验证带 `[自动]`
条目。核心条目：

- [ ] `result.json` 有 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/
      change_log（结果完整性/可核验性）。
- [ ] 每个框/箭头/条形/图例色块映射到一条 entity/relation/data 记录且带 `source`
      locator+quote（实体关系与数据映射正确）。
- [ ] 箭头方向与来源句一致；数值带单位、比例尺、条件标签（结果正确性）。
- [ ] SVG 分层命名、`<text>` 可编辑、无 text-as-path；PNG/PDF 为真实文件或如实
      `preview_not_rendered`（可编辑源文件/导出件）。
- [ ] 无画布溢出、字号 ≥8px、色觉安全且灰度可区分（版式与视觉层级）。
- [ ] 同一输入重复运行结构一致；change_log 每次改写递增（运行稳定性/可复核）。
- [ ] 缺失/歧义项进 `uncertainty/limitations/pending_human`，从不编造（异常处理）。
- [ ] 脚本仅标准库、相对路径 `scripts/*.py`；无隐藏个人文件（生态贡献/复用性）。

## 6. 降级与诚实策略

- **缺数据/值**：字段置 `null`，加 `uncertainty` 条目，不估算。
- **实体/方向歧义**：拆 id 或使用 undirected 连接，注明歧义。
- **缺渲染器/字体**：保留 SVG 源文件，`exports.status` 记 `preview_not_rendered` 或
  `failed`，写 `limitations`；用平台 office-renderer / pdf-render-tool / browser-automation
  尝试，**禁止伪造 PNG/PDF**。
- **无 `case.json`**：`extract_case.py` 写 `status=failed` 骨架并退出 1；停止下游步骤并报告
  blocker。
- **字体替换/导出失败**：写进 `limitations`；可编辑 SVG 仍然计为主产物。
- 不在任何字段伪造运行分数、模型名、时间或科学数据；不确定就写 `not_reported` 或
  `pending_human`。

## 7. 包结构（文件数 5–8、总体积受平台约束、脚本仅标准库）

```text
outputs/
├── SKILL.md                     # 本文件（frontmatter + 正文 ≤180 行）
├── scripts/
│   ├── extract_case.py          # 归一化 + contract 骨架（先落盘）
│   ├── main.py                  # build / export / validate / finalize（<30KB）
│   └── selftest.py              # 空输入、坏JSON、缺case、含contract 四场景自检
└── references/
    ├── output_schema.md         # canonical 输出契约与逐字段规范
    └── checklist.md             # 逐条自检清单
```

## 8. 最小调用示例（示意输出，非正式案例答案）

```bash
cd /skills/weekly-skill
python -B scripts/extract_case.py --input /input --output /app/output --work /app/work
python -B scripts/main.py build --output /app/output --work /app/work
python -B scripts/main.py export --output /app/output --work /app/work
python -B scripts/main.py validate --output /app/output --work /app/work
python -B scripts/main.py finalize --output /app/output --work /app/work
python -B scripts/selftest.py     # 交付前自检，退出码 0 为通过
```

示意 `result.json`（**示例**，仅说明接口；字段以 `/input/case.json` 的 `output_contract`
为准）：

```json
{
  "case_id": "example_case",
  "status": "completed",
  "graphical_abstract": {"question": null, "method": null, "results": null, "conclusion": null},
  "editable_source": {"path": "fig_example_case.svg", "format": "svg",
                      "layers": ["layer-entities", "layer-relations"], "text_editable": true},
  "exports": [{"path": "fig_example_case.png", "format": "png", "renderer": "rsvg-convert",
               "status": "rendered", "width_px": 1200}],
  "entities": [], "relations": [], "data_mapping": [], "legend": [],
  "sources": [], "uncertainty": [], "limitations": [], "pending_human": [],
  "errors": [], "degradations": [], "runtime": {"seconds": null, "model_requests": null},
  "change_log": [{"version": 1, "time_utc": "2026-01-01T00:00:00Z",
                  "what": "skeleton persisted", "why": "save early"}]
}
```
