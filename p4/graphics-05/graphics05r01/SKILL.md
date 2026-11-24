---
name: graphical-abstract-evidence
description: >-
  面向科研图形摘要（graphical abstract）的 Skill。从 /input/case.json 与 task.md 及材料文件
  （PDF/CSV/图）中提取实体、关系、数据与图面约束，构建分层可编辑 SVG，交付真实 PNG/PDF 预览，
  并写入逐条带 locator 与逐字 quote 的 evidence_map，最终产出 /app/output/result.json。
  适用于图形摘要、机制图、流程图与图表编辑类任务；证据溯源为强约束，不允许编造数值或来源。
version: 1.1.0
---

# 科研图形摘要 Skill（证据溯源强化版）

## 1. 角色与何时使用

你是一名科研图稿作者。给定一个固定案例，产出一页「研究问题→方法→核心结果→结论」的图形摘要，
同时交付**可编辑分层源文件**与**真实渲染预览**，每个图元都能回溯到来源材料。仅用于图形摘要、
示意图、机制图、流程图、图表编辑任务；不写论文正文、不做统计分析或实验设计。

## 2. 输入契约

- 运行时输入：`/input/case.json`（案例事实、材料、输出契约）、`/input/task.md`（题目文本）；
  材料 PDF/CSV/图可能也在 `/input/` 下。**读真实文件，不信任文件名或标签**；PDF 文本来自 PDF 本体，
  没有预提取答案层。
- 工作文件写 `/app/work/`；最终产物写 `/app/output/`。
- Skill 安装在 `/skills/weekly-skill/`；脚本用相对路径 `scripts/*.py` 调用，运行时以
  `/skills/weekly-skill` 为 cwd（`cd /skills/weekly-skill` 后再运行）。
- 若 `case.json` 带 `output_contract`，按其字段原样回填；缺值填 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

必须写 `/app/output/result.json`，含 `case_id`、`status`、主产物、`sources`、`uncertainty`、
`pending_human`、`change_log`，并**原样回填**题目 `output_contract` 的全部字段。规范字段如下：

- `graphical_abstract`: `{question, method, results, conclusion}`，各为 `{text, evidence_ref}`。
- `editable_source`: `{path, format:"svg", layers:[], text_editable:true, renderer}`；分层 SVG
  `fig_<case_id>.svg`，`layer-*` 命名组，文字保持 `<text>`，元素带 `data-source`/`data-locator`。
- `exports`: `{png:{path,status,renderer}, pdf:{path,status,renderer}}`；真实文件；无渲染器则
  `preview_not_rendered`，绝不写假图。
- `entities` / `relations` / `data_mapping` / `legend`：每项带 `id`、`source_ref`、`locator`、`quote`。
- `evidence_map`: `{claims:[], conflicts:[], unresolved:[]}`，是核心字段，见第 5 节。
- `sources`: `[{source_id, path, type, title}]`；`uncertainty`、`pending_human`、`limitations`、
  `errors`、`degradations`、`runtime`、`change_log` 均为数组或对象。
- `status` 仅取 `completed | partial | failed`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

**硬规则：前 3 分钟内必须落盘一版合法 `result.json` 骨架（status=partial），此后只做增量改进。**

| 步骤 | 时间/请求 | 输入 → 输出 |
|-|-|-|
| 1 归一化并落盘骨架（≤60s，≤6 req） | `python scripts/main.py --input /input --output /app/output --work /app/work` → `case_normalized.json`、`figure_spec.json` 模板、`result.json` 骨架 | 
| 2 提取事实与证据（≤90s，≤12 req） | 用 PDF/text 工具读材料，把实体/关系/数据/图面约束及逐条证据写入 `/app/work/figure_spec.json` 与 `/app/work/evidence_map.json` |
| 3 构建图稿（≤60s，≤6 req） | `python scripts/build_svg.py --spec /app/work/figure_spec.json --out /app/output` → SVG + `fig_manifest.json` + 预览状态 |
| 4 校验并映射来源（≤60s，≤8 req） | 重跑 `main.py` 或 `scripts/selftest.py --check-result /app/output/result.json`；补全 `sources`/`evidence_map`；修关键缺陷 |
| 5 增量改进（≤6 min，≤20 req） | 检查报告 → 修溢出/小字/低对比/缺证据 → 重复 3–4；每次改动追加 `change_log` |
| 6 定稿（≤60s，≤6 req） | 定 SVG/PNG/PDF 与报告 → 设 `status`、`limitations`/`uncertainty`，写最终 `result.json` |
| — 预留（~2 min，~6 req） | 错误处理、渲染回退、复核 |

请求上限 64 次；同策略失败两次即停止重试，如实记录 limitation 并降级为 `partial`。

## 5. 证据溯源规则（本版强化：每条结论 locator + 逐字 quote）

- **每个关键结论、数值、图元必须能指回材料的逐字片段**：`locator`（如 `materials/xxx.pdf#p3`、
  `materials/xxx.csv#L12`）+ `quote`（原文字符串）+ `source_ref`。写不进 `quote` 就写 `not_reported`，
  不得改写或拼接成伪引用。
- `evidence_map.claims[]` 每项含：`claim_id`、`text`、`kind`（`conclusion|data|entity|relation`）、
  `support_level`（`direct|inferred|ambiguous|not_reported`）、`locator`、`quote`、`source_ref`、
  `maps_to`（对应 entity/relation/data id 列表）、`confidence`、`note`。
- 图面元素与证据双向可核验：实体/关系/数据/图例每项都写 `locator`+`quote`；`maps_to` 反向指向图元。
- 同一指标来源不一致时，两处都记录并标 `source_conflict`；缺失写 `pending_human`，绝不编造数值、DOI、引用。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] `result.json` 含 `case_id` + 全部 output_contract 字段 + status/sources/uncertainty/pending_human/change_log。
- [ ] 每个框/箭头/柱/图例色块映射到唯一 entity/relation/data 条目，条目带 `sources` 指针与 locator+quote。
- [ ] 箭头方向与原文因果一致；单位、比例尺、条件标签齐全。
- [ ] SVG 分层、命名、文字可编辑、无 text-as-path；PNG/PDF 为真实文件或 `preview_not_rendered`。
- [ ] 无画布溢出、字号 <8px、低对比色；已查色觉差异/灰度。
- [ ] `evidence_map` 每条结论有 locator+逐字 quote，冲突/未决已标注。
- [ ] 同一输入复现同一结构；`change_log` 每次重写追加。
- [ ] 缺失/歧义进 `uncertainty`/`limitations`，不编造。
- [ ] 脚本相对路径 `scripts/*.py`、仅标准库，无个人隐藏文件。

## 7. 降级与诚实策略

- 缺数据/数值：字段填 `null`，加 `uncertainty`，不估算。
- 实体/方向歧义：拆分 id 或用无向连接，注明歧义。
- 缺渲染器/软件：保留 SVG，记录 `preview_not_rendered`，用平台 pdf-render-tool / office-renderer /
  browser-automation 补真实预览；不伪造 PNG。
- 无 `case.json`：`main.py` 写 `status=failed` 骨架并退出码 1；报告阻塞并停止依赖步骤。
- 字体替换/导出失败：写入 `limitations`；可编辑 SVG 仍计为主产物。
- 空输入/坏 JSON/坏编码：降级为合法结果（`insufficient_evidence`/`failed`），记录到 `errors`/`degradations`。

## 8. 脚本与自检

- `scripts/main.py`：归一化、写骨架、构建、校验、回填 result.json（`--input/--output/--work` 缺省为
  `/input`、`/app/output`、`/app/work`）。
- `scripts/build_svg.py`：由 `figure_spec.json` 确定性生成分层 SVG、manifest 与诚实导出状态。
- `scripts/selftest.py`：包级自检（frontmatter、脚本编译、空输入跑通、SVG/JSON 合法性），也可
  `--check-result` 校验已产出结果。
- 运行：`python -B scripts/selftest.py`（退出码 0 为通过）；`python -B scripts/main.py --help` 查看参数。

## 9. 最小调用示例（示意，非正式案例结果）

输入 `/input/case.json`（示意）：

```json
{"id":"selftest-synthetic","task":"synthetic","materials":[{"name":"note.md","type":"text"}],
 "output_contract":{"custom_note":null}}
```

输入 `/input/task.md`（示意）：`# 示意任务\n仅示例。\n`。调用：

```bash
cd /skills/weekly-skill
python -B scripts/main.py --input /input --output /app/output --work /app/work
python -B scripts/selftest.py --check-result /app/output/result.json
```

示意 `result.json` 骨架（**示例，标注来源，不冒充正式结果**）：

```json
{"case_id":"selftest-synthetic","status":"partial",
 "graphical_abstract":{"question":{"text":"…","evidence_ref":"s1"},"method":{"text":"…","evidence_ref":"s1"},
   "results":{"text":"…","evidence_ref":"s1"},"conclusion":{"text":"…","evidence_ref":"s1"}},
 "editable_source":{"path":"fig_selftest-synthetic.svg","format":"svg","layers":["layer-panels","layer-entities"],
   "text_editable":true,"renderer":"stdlib"},
 "exports":{"png":{"path":null,"status":"preview_not_rendered","renderer":null},
   "pdf":{"path":null,"status":"preview_not_rendered","renderer":null}},
 "entities":[{"id":"e1","label":"A","source_ref":"s1","locator":"note.md#L1","quote":"仅示例。"}],
 "relations":[{"id":"r1","from":"e1","to":"e2","kind":"arrow","label":"causes",
   "source_ref":"s1","locator":"note.md#L2","quote":"A 导致 B"}],
 "data_mapping":[],"legend":[],
 "evidence_map":{"claims":[{"claim_id":"c1","text":"A 导致 B","kind":"conclusion",
   "support_level":"direct","locator":"note.md#L2","quote":"A 导致 B","source_ref":"s1",
   "maps_to":["r1"],"confidence":"high","note":"示例"}],"conflicts":[],"unresolved":[]},
 "sources":[{"source_id":"s1","path":"note.md","type":"text","title":"示意"}],
 "uncertainty":[],"pending_human":[],"limitations":[],"errors":[],"degradations":[],
 "runtime":{},"change_log":[{"rev":1,"action":"rebuild+validate"}]}
```

## 10. 依赖、限制与维护

- 依赖：仅 Python 3 标准库，无第三方包；不需要渲染器即可产出 SVG。
- 导出参数：SVG 默认 1600×900，字体族 `DejaVu Sans, Arial, sans-serif`；PNG/PDF 由平台渲染工具生成后回填
  `exports`，脚本本身不伪造位图。
- 限制：科学事实抽取与最终判断由 Agent 完成，脚本只做确定性布局、构建与校验；证据 quote 必须由 Agent
  从材料逐字摘取，脚本不代读 PDF。
- 维护：修改图面规则只改 `scripts/build_svg.py`；修改契约只改 `scripts/main.py` 的 `CANONICAL_FIELDS`；
  提交前运行 `python -B scripts/selftest.py`。

## 11. 常见错误与规避（结果正确性）

- 实体混淆：分子/细胞/组织/材料/器件/样本层级须用 `type` 标注并分面板，避免张冠李戴与层级错位。
- 通路方向反：箭头 `from→to` 必须与原文主语→宾语一致；抑制/激活/转化用 `kind` 区分，不一律画成激活。
- 图例与编码不符：`data_mapping[].channel`（颜色/长度/面积/亮度）必须与 `legend[].swatch` 同一视觉通道。
- 图中数字无来源：删掉该数字并记 `uncertainty`，或改标 `not_reported`，不凭记忆补值。
- 画布溢出/小字：实体坐标 + 宽高须在 canvas 内；正文字号 ≥16px、图注 ≥11px，禁用 <8px。
- 字体替换/导出失败：记录 `limitations`；分层 SVG 文字仍可编辑，主产物不受影响。
