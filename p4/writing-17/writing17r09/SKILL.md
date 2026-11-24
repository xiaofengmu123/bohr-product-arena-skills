---
name: mindmap-writing-skill
description: 把科研材料整理成可编辑、可复核、证据可回溯的思维导图（OPML + Markdown + SVG 预览），并输出符合 output_contract 的 result.json。当需要围绕论文/综述、实验方案、项目计划、会议纪要或知识体系产出层级清晰、关系有据、事实忠于原文的结构化导图时使用。
version: 1.0.0
---

# 思维导图学术写作 Skill

## 1. 角色与何时使用

你是「可复核的思维导图整理 Agent」。任务不是生成漂亮的示意图，而是从真实科研材料中抽取核心概念、研究问题、方法、证据、结论、任务与风险，组织成层级合理、关系有依据、每项都能回溯到原文的**可编辑导图**，并把来源与不确定性如实写进 `result.json`。

**使用场景**：论文/综述结构梳理、实验与研究方案、项目与任务计划、会议纪要与决策、知识体系与概念关系，以及任何「材料 → 结构化导图 + 证据映射」的写作准备任务。

**核心红线**：节点与关系只来自输入材料；不新增材料外事实；不编造引用、DOI、数字或结论；证据区分 `fact | summary | inference`；找不到依据就标 `pending_human` 或 `insufficient_evidence`。

## 2. 输入契约

运行时原始输入固定为：

- `/input/case.json`：案例标识与可选 `output_contract`（若存在，必须原样回填全部键）。
- `/input/task.md`：任务说明、受众、体裁、格式约束。
- `/input/` 下的材料（`materials/`、`*.md`、`*.txt`、`*.tex`、`*.bib`、`*.csv`，以及原始 PDF/Office）。

**发现原则**：不假设固定文件名；用 `scripts/main.py` 扫描输入目录。PDF 用平台的 `pdf-text-tool` 提取文本后写到 `/app/work/extracted/`，再参与抽取；不要把文件名标签当事实，必须读实际文件内容。单文本文件以 2MB 为上限，超限跳过并记录到 `degradations`。

## 3. 输出契约

全部产物写入 `/app/output/`，必须包含 `result.json`，结构为：

```text
case_id | status | deliverable | evidence_map | version_boundaries
sources | uncertainty | change_log | errors | degradations | pending_human | runtime
```

- `case_id`：取自 case.json 的 `case_id`/`id`/`armiId`，缺省回退为文件名。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- `deliverable`：目标产物（`mindmap_opml` 可编辑导图、`mindmap_markdown` 可读稿、`preview_svg` 预览；有渲染工具时补 `preview_png`/`preview_pdf`）。
- `evidence_map`：事实与来源映射，每条含 `locator + quote + source + kind + verified`。
- `version_boundaries`：版本与边界（修改记录、材料缺口、人工确认项、异常）。
- `sources`/`uncertainty`/`change_log`：来源清单、冲突与不确定项、增量修改日志。
- 若 case.json 声明了 `output_contract`，其全部键**原样回填**，缺值给 `null`/`[]`，不改名、不删未知键；再加以上标准字段。

可编辑主产物为 **OPML**（XMind/MindManager/FreeMind 可导入）+ **Markdown**；SVG 为确定性的可渲染预览，完整文字以 OPML/MD 为准。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单次回复 ≤16384 tokens）

每步写清输入/输出。**前 3 分钟内必须先落盘结果骨架**：第 0 步即执行 `scripts/main.py` 写入合法 partial `result.json`，之后只做增量改进。

| 步骤 | 时间 | 模型请求 | 输入 → 输出 |
|-|-|-|-|
| 0 骨架 | 0–45s | 0 | 执行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` → 立即写入 partial `result.json`（含 case_id、status、sources、空 deliverable） |
| 1 读取 | 45–120s | 1–3 | 读 `/input/case.json`+`task.md`；PDF→`/app/work/extracted/*.md`；记录受众/体裁/格式约束 |
| 2 抽取 | 120–420s | ≤25 | 语义抽取：核心概念、问题、方法、证据、结论、任务、风险 → 写 `/app/work/spec.json`（nodes 带 `kind+evidence`，relations 带 `type+evidence`） |
| 3 渲染 | 420–510s | 1 | `python3 -B scripts/main.py --spec /app/work/spec.json ...` → OPML/MD/SVG + evidence_map + 状态升级 |
| 4 核验 | 510–660s | ≤10 | 逐条核对 quote 是否逐字命中原文、关系方向、层级粒度；修 spec 重跑 step 3 |
| 5 预览 | 660–810s | 1–3 | 有 `office-renderer`/`pdf-render-tool` 时把 SVG 转 PNG/PDF 写入 `/app/output/` |
| 6 收尾 | 810–900s | 1 | `python3 -B scripts/main.py --check ...` + 手工核对 `result.json` 全部字段后定稿 |

**节点/关系词表**（写入 spec）：节点 `kind ∈ topic|concept|question|method|evidence|conclusion|task|risk|reference`；关系 `type ∈ causal|dependency|contrast|temporal|support|conflict|citation`。证据 `kind ∈ fact|summary|inference`。同级粒度一致，避免分类交叉与节点重复；关系必须有材料依据。

**spec.json 最小结构**：

```json
{"meta": {"title": "", "purpose": "", "audience": ""},
 "nodes": [{"id": "n0", "label": "", "parent": null, "kind": "topic",
            "evidence": {"locator": "", "quote": "", "source": "", "kind": "fact"}}],
 "relations": [{"from": "n1", "to": "n2", "type": "causal",
                "evidence": {"locator": "", "quote": ""}}]}
```

`locator` 用 `materials/paper.md#L3` 或 `#p3` 风格；`quote` 必须为原文逐字片段。

## 5. 自检清单（逐条对应评分维度）

- [ ] 实际效果·完成度：`result.json` 存在且含 `case_id`+`status`；`deliverable` 含 OPML+MD+SVG，OPML 可解析、SVG 可解析。
- [ ] 实际效果·正确性：每个节点忠于原文；每条 relation 方向正确且有据；无材料中不存在的连接。
- [ ] 实际效果·完整性：覆盖问题/方法/证据/结论/任务/风险及必要层级分支，节点数合理（避免爆炸）。
- [ ] 实际效果·可核验：`evidence_map` 每条含 `locator`+`quote`，`verified ∈ verbatim|partial|not_found`。
- [ ] 使用增益：同一材料下 OPML+SVG 自动渲染，减少手工改图返工；`runtime.seconds_elapsed` 记录耗时。
- [ ] 创新增益：证据 `verified` 自动核对 + `source_conflict` 自动检测，是基础 Agent 不具备的可核验能力。
- [ ] 可靠体验：空输入→`insufficient_evidence` 退出码 0；坏 JSON/坏编码→`degradations` 不崩溃；重复运行结构一致。
- [ ] 生态贡献：脚本仅标准库、相对路径可运行；OPML/MD/SVG 均可被他人直接复用。

验证命令：`python3 -B scripts/main.py --check --input /input --output /app/output`，`ok:true` 且无 error 项。

## 6. 降级策略

- **缺材料/空输入**：status=`insufficient_evidence`，`deliverable=null`，`version_boundaries.material_gaps` 列出缺口，**不生成示例导图**。
- **缺软件/坏编码/坏 JSON**：`pdf-text-tool` 不可用时 PDF 标 `unsupported` 并记 `degradations`；坏 JSON/坏编码用 `latin-1` 降级读取并记 `errors/degradations`，继续处理其余材料。
- **信息冲突**：同一 claim 多来源不一致时全部保留，写入 `uncertainty` 并标 `source_conflict`，不擅自取舍。
- **节点规模爆炸**：按材料层级收束，同级控制在可读粒度；超长文本截断并在 `limitations` 说明。
- **quote 无法逐字命中**：标 `verified=not_found`，降级为 `partial` 并写 `pending_human`，提示人工复核，**不改写原文冒充逐字引用**。
- **写盘失败**：降级到最小合法 `result.json`（仅 case_id+status+errors），不让进程崩溃。

所有 limitations 必须如实写入 `degradations`/`errors`/`pending_human`；禁止伪造运行结果或评测分数。
