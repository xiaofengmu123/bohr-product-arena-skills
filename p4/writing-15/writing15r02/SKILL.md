---
name: long-doc-writing
description: 面向学位论文与长文档学术写作的 Agent 工作流：读取 /input 材料与任务、按提纲生成或改写章节、统一术语/符号/编号/引用、为每个论断保留逐字来源与改动记录、产出可复核的 result.json 与可编辑稿件。当需要在给定科研材料上完成长文档写作、改写、校对、引用或结构化任务时使用。
version: 1.0.0
---

# 长文档学术写作 Skill

## 1. 角色与何时使用

本 Skill 指导 Agent 在给定科研材料上完成学位论文 / 长文档写作任务，目标是“立得住、连得上、改得动、查得清”：章节规划对应提纲与论证链；跨章节术语/定义/符号/编号/引用一致；局部修改可解释、可回退；每个论断、每条引用、每次改动可追溯到原文或版本记录。

适用：case.json 给出任务说明与科研材料、要求产出稿件 / 改稿 / 引用表 / 一致性报告等可编辑产物的写作题。
不适用：纯问答、一次性脚本、无材料的凭空生成。无材料时必须返回 `insufficient_evidence`，绝不编造内容、数据、引用或 DOI。

## 2. 输入契约

运行时（平台 Harness 提供，以实际文件为准，勿按标签假设内容）：

- `/input/case.json`：任务说明；可能含 `output_contract`、材料清单、评分证据范围。
- `/input/task.md`：题目与运行契约。
- `/input/` 其余文件：科研材料（`.md/.txt/.json/.csv/.tsv/.tex/.bib/.yaml/.pdf`）。PDF 用平台 PDF 工具解析，无预抽取答案层，必须读真实内容。若存在 `organizer-edit-workorder.json`，其编辑目标即本案例主任务；源材料可能是分章节可编辑工作稿而非完整论文。
- `/app/work/`：中间产物；`/app/output/`：最终产物。

约束：不假设固定文件名/键名，先扫描再解析；文本按 utf-8-sig→utf-8→gb18030→latin-1 逐级降级读取；单文件 >2MB 跳过并记入 `degradations`；若 case.json 含 `output_contract`，其全部键必须原样回填（缺值 `null`/`[]`，不改名、不删未知键）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8），顶层至少含：

- `case_id`（来自 case.json 的 id/case_id/challenge_id）
- `status`：`ok | partial | insufficient_evidence | input_error`
- `output_contract`：题目 output_contract 全字段，原样回填
- `target_deliverable` / `evidence_map` / `version_and_limits`：目标产物、事实与来源映射、版本与边界
- `artifacts`：主产物路径（相对 `/app/output`）及说明
- `sources`：每项含 `locator` + 逐字 `quote` + `claim`
- `uncertainty`：每项含 `point`/`reason`/`status`
- `change_log`：每次改动的 `before`/`after`/`why`
- `errors`、`degradations`、`pending_human`、`runtime`

主产物格式：文本类给 `.md/.json/.csv`；图形给可编辑 SVG + 真实 PDF；Office/幻灯片/图表给 `.docx/.pptx/.xlsx` + 真实渲染 PDF/PNG 预览。只能完成部分时 `status=partial` 并写清 limits；材料不足为 `insufficient_evidence`；不编造缺失的软件状态或科学数据。

## 4. 执行流程（总预算 900 秒、64 次模型请求、每次响应 ≤16384 token 含推理）

以下命令在 Skill 根目录（`/skills/weekly-skill`）下执行；脚本仅依赖 Python 标准库。

**Phase 0 — 立即落盘骨架（0–90 秒，必须 <3 分钟）**

```bash
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
```

写 `/app/work/context.json`（材料清单与预览）和 `/app/output/result.json` 骨架（`partial` 或 `insufficient_evidence`）。此后任何中断都留有合法结果，不得跳过。

**Phase 1 — 读取与证据抽取（90–300 秒，约 8–14 次请求）**
按 context.json 清单读取材料；抽取可验证事实（数字/术语/引用/图/关键论断），每项记 `locator + 逐字 quote`，分批写入 `/app/work/updates/evidence_partial.json`。避免反复通读全文；用局部读取回看。

**Phase 2 — 写作主产物（300–600 秒，约 20–30 次请求）**
按题目体裁/受众/格式约束生成可编辑 Markdown 稿 → `/app/output/<slug>.md`；关键论断以 `[src:<locator>]` 标注。术语表、引用表、改动记录作为独立 JSON/CSV 落盘。

**Phase 3 — 一致性与自检（600–840 秒，约 6–10 次请求）**

```bash
python3 -B scripts/main.py consistency --work /app/work --output /app/output
```

检查术语变体、图/表/章节编号连续性、引用与交叉引用解析，报告写入 work 并并入 result。可自动修复项立即修；不可判定的写 `pending_human`。

**Phase 4 — 汇总与校验（840–900 秒，约 2–4 次请求）**
把最终字段写入 `/app/work/updates/result_update.json`，然后：

```bash
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```

合成并校验 result.json，确认 artifacts 存在、JSON 可解析。全程先写部分产物再增量改进，禁止最后才一次性生成全文。

## 5. 自检清单（提交前逐条勾选，对应评分维度）

- [ ] result.json 可被 `json.load` 解析，含 case_id/status/sources/uncertainty/change_log（可核验性/完整性）
- [ ] output_contract 全部键已回填，未改名、未删未知键（结果完整性）
- [ ] 每条关键论断/数字/术语/引用有 locator + 逐字 quote；冲突信息标 `source_conflict`（正确性/可核验性）
- [ ] 主产物为可编辑格式；Office/图形有真实渲染预览（可编辑性）
- [ ] 术语、符号、编号、图/表/章节交叉引用一致（跨章节一致性）
- [ ] 材料缺口、无法判断、异常分别写入 errors/degradations/pending_human，不编造（异常处理）
- [ ] 同一输入重复运行结果一致（脚本确定、无随机）（稳定性）
- [ ] `python3 -B scripts/main.py selfcheck` 退出码 0；空目录/坏 JSON 各跑一次返回合法结果（健壮性）

## 6. 降级与失败策略

- 缺材料：`status=insufficient_evidence`，`evidence_map=[]`，`pending_human` 列缺口，不填充示例内容。
- 坏编码/坏 JSON：记录到 `errors`/`degradations`，继续处理其余材料。
- 缺软件（LaTeX/Office 渲染器不可用）：跳过依赖步骤，`limitations` 标注“未渲染预览”，保留可编辑源文件。
- 缺参数：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- 无法判定（引用找不到原文、结论冲突、编号不连续）：写 `uncertainty` + `pending_human`，不改写原文掩盖问题。
- 超长材料：>2MB 跳过并记录，不擅自截断后补造内容。

## 7. 脚本调用

```bash
python3 -B scripts/main.py scaffold    --input /input --output /app/output --work /app/work
python3 -B scripts/main.py consistency --work /app/work --output /app/output
python3 -B scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck
```

`scripts/consistency.py` 可独立复用：`python3 -B scripts/consistency.py --text <file> [--termlist <file>] [--bibliography <file>]`。
