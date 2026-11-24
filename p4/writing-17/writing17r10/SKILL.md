---
name: sci-mindmap
description: 把科研材料（论文、综述、研究方案、会议纪要、知识资料、任务说明）整理为可编辑、可溯源、可渲染的思维导图：抽取核心概念/研究问题/方法/证据/结论/任务/风险节点，组织合理层级，标注因果·依赖·对比·时间·引用·支持·冲突关系，逐字挂接来源证据，并产出 SVG、PDF、FreeMind(.mm)、Markdown、JSON 多格式产物。当任务要求"思维导图 / 概念梳理 / 结构整理 / 信息层级化 / 研究梳理"时使用。
version: 1.0.0
---

# 科研思维导图 Skill（sci-mindmap）

## 1. 角色与何时使用

你是"科研信息 → 可复核思维导图"的整理者。适用场景：题目要求围绕给定科研材料产出一份思维导图，或需要把论文/综述/方案/纪要/知识资料梳理成层级结构。**不适用**：纯问答、无需产物的一次性脚本、已有明确格式但与思维导图无关的写作任务。

核心目标五条（对应评分重点）：

- **抓得准**：只抽取核心概念、研究问题、方法、证据、结论、任务、风险、局限/研究空白，不把装饰性词当节点。
- **理得清**：单根、层级合理、同级粒度一致，不交叉、不重复、不埋没关键信息。
- **连得对**：关系只写材料有依据的，方向正确；因果/依赖/对比/时间/引用/支持/冲突各归其类。
- **证据可追溯**：每个关键节点与关系挂 `locator + quote`，并标 `fact|summary|inference`，不新增材料外事实。
- **实际有用**：产物可打开、可读、可继续编辑，可复核，运行成本可控。

节点与关系的精确定义见 `references/node_relation_schema.md`；自检清单见 `references/selfcheck_checklist.md`。

## 2. 输入契约

运行时原始输入在 **`/input/case.json`** 与 **`/input/task.md`**；材料（论文 PDF 等）按题目放在 `/input/materials/` 或 case.json 指定的路径。工作目录为 **`/app/work`**，最终产物写 **`/app/output/`**。**不得假设固定文件名**：先扫描 `/input` 全目录，识别 case.json、task.md、materials/、notes 等。PDF 用平台 `pdf-text-tool` 抽取真实文本；**不依据文件名标签补造内容**，一切以真实文件内容为准。

Skill 根目录通常为 `/skills/weekly-skill`（先 `echo $SKILL_PATH` 或用工具确认；找不到时用 `/skills/weekly-skill`）。所有脚本用相对路径调用：

```bash
cd "${SKILL_DIR:-/skills/weekly-skill}"
python3 -B scripts/scaffold.py --input /input --output /app/output --work /app/work
```

一步编排入口（scaffold → render → selfcheck）：`python3 -B scripts/main.py --input /input --output /app/output --work /app/work`。各脚本也可单独运行。

## 3. 输出契约（/app/output/result.json 必须满足）

`result.json` 必须含：`case_id`、题目 `output_contract` 的**全部字段（原样回填，缺值给 null/[]，不改名、不删未知键）**，再加标准字段：

```json
{
  "case_id": "...",
  "status": "ok | partial | insufficient_evidence | input_error",
  "<output_contract 的每个键>": null,
  "sources": [{"path": "...", "role": "...", "bytes": 0}],
  "uncertainty": [{"target": "...", "issue": "...", "resolution": "..."}],
  "change_log": [{"ts": "...", "step": "...", "summary": "..."}],
  "errors": [], "degradations": [], "pending_human": [],
  "runtime": {"started_at": "...", "seconds": 0},
  "artifacts": {"mindmap_svg": "...", "mindmap_pdf": "...", "mindmap_mm": "...", "mindmap_md": "...", "mindmap_json": "...", "preview_png": null}
}
```

主产物（写到 `/app/output/`，可编辑 + 可预览 + 可复核）：

- `mindmap.json` — 结构化源（title/root/relations/metadata），可程序化再编辑
- `mindmap.svg` — 真实渲染、矢量可编辑、浏览器可直接打开（**主预览**）
- `mindmap.pdf` — PDF 预览（见第 6 节降级说明）
- `mindmap.mm` — FreeMind XML，XMind/FreeMind 可继续编辑（含"关系"分支与证据 note）
- `mindmap.md` — 可读层级大纲 + 关系与证据段落
- `preview.png` — 若平台 `pdf-render-tool`/`office-renderer` 可用则生成，否则留 null

`status` 必须诚实：材料不足就 `insufficient_evidence`，格式损坏就 `input_error`，缺软件能力就 `partial` 并写 `degradations`，**绝不编造数据或结论**。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求 / 每响应 ≤16384 token）

**第 0 阶段（0–3 分钟内，约 4–6 次请求）：先落盘骨架，绝不裸奔。**
1. 扫描 `/input`，列出输入清单。
2. 运行 `scripts/scaffold.py`：读取 case.json，解析 `output_contract`，立即写 `/app/output/result.json`（status=partial，全部 contract 键回填 null，case_id/sources/change_log/runtime 就位）。
3. 写 `/app/work/mindmap.json` 最小版：根节点=主题 + 材料清单 + 2–4 个初步一级分支（research_question/method/evidence/conclusion 等），运行 `scripts/render_mindmap.py` 产出第一版 SVG/PDF/MM/MD。
4. 之后只做**增量改进**：任何中途异常都不会留下空目录。

**第 1 阶段（3–6 分钟，约 15–25 次请求）：抽取与建树。**
5. 逐块精读材料（分块读，控制 token），按五类节点提取 + 逐字证据，把 `mindmap.json` 填完整（每节点带 id/kind/evidence）。
6. 设计层级：单一根；每层粒度一致；合并重复节点；同级按逻辑排序。
7. 标关系：只写有依据的因果/依赖/对比/时间/引用/支持/冲突，逐条挂证据。

**第 2 阶段（6–10 分钟，约 20–30 次请求）：复核与回填。**
8. 核对每个 `locator + quote` 能指回材料原文；冲突信息两个都记并标 `source_conflict`；材料没有的标 `not_reported`/`pending_human`。
9. 重跑 `scripts/render_mindmap.py`；用 `pdf-render-tool` 生成 PNG（可用时）。
10. 把 result.json 从 partial 填成 ok：contract 字段写真实产物与证据映射，`uncertainty`/`change_log` 如实更新。

**第 3 阶段（10–15 分钟，约 5–10 次请求）：自检与收尾。**
11. 运行 `scripts/selfcheck.py`，逐条核评分维度，修正 FAIL。
12. 确认 `result.json` 所有 contract 键非空或已诚实标 null + 理由；写最终 change_log。

每阶段先落盘再改进；任何一步失败都记录到 `errors`/`degradations` 并继续其余可做部分。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

- [ ] 触发正确：SKILL.md 有合法 name/description/version，Agent 能按第 2 节找到并执行脚本（易用性）
- [ ] 输入健壮：空目录、坏 JSON、坏编码、缺 output_contract 均不崩溃，且写合法 result.json（稳定性/异常处理）
- [ ] 骨架先行：3 分钟内已落盘 partial result.json 与第一版导图（稳定性）
- [ ] 抓得准：节点均为核心概念/问题/方法/证据/结论/任务/风险，无装饰词（正确性）
- [ ] 理得清：单根、层级≤5 层、同级粒度一致、无重复/交叉节点（完整性）
- [ ] 连得对：每条关系方向正确、类型归位、材料有据，无凭空连线（正确性）
- [ ] 证据可追溯：关键节点/关系有 locator+quote+claim_type，事实/概括/推断区分清楚（可核验性）
- [ ] 产物可编辑：SVG 可解析、PDF 以 `%PDF-` 开头、.mm 可解析、.md/.json 非空（完成度）
- [ ] 契约回填：result.json 含 case_id + output_contract 全部字段（完整/可核验）
- [ ] 诚实降级：缺数据/缺软件时 status 与 limitations/degradations 如实标注，不编造（异常处理）

## 6. 降级策略

- **缺数据/材料不足**：`status=insufficient_evidence`，在 `uncertainty`/`pending_human` 列出缺口，导图只保留有据部分，不填充示例内容。
- **坏 JSON/坏编码**：脚本按 `utf-8-sig` 读取并降级（`errors='replace'`），单个 >2MB 文件跳过并记录；仍写合法 result.json。
- **缺渲染软件**：`mindmap.svg` 始终由纯标准库产出（主预览）；`mindmap.pdf` 由纯标准库最小 PDF 写出器生成**拓扑预览**（ASCII 标签，页脚注明"全文见 mindmap.svg"）；无 `pdf-render-tool` 时 `preview.png=null` 并记入 `degradations`，不得伪造 PNG。
- **缺参数/未知参数**：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- **写盘失败**：降级到最小合法产物（至少 result.json 的骨架），进程不崩溃，错误记入 `errors`。
- **节点规模爆炸**：单层节点 >40 时按重要度裁剪或拆分子图，`metadata` 记录裁剪策略。

## 7. 边界

一个 Skill 只解决"材料 → 可复核思维导图"这一类任务，不做通用大而全流程。无真实材料时输出"材料不足"而非示例填充。提交前在非作者环境（不同 Python 版本）跑一遍第 5 节自检。
