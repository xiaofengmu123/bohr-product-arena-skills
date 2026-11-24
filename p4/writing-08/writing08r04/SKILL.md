---
name: methods-experiments-writing
description: 把零散实验材料（代码、配置、日志、数据说明、笔记）写成可复现、可复核的论文 Methods/Experiments 章节，覆盖方法步骤、实验设置、参数、数据集与划分、对照实验、可复现与限制六类要素；每条事实挂逐字来源与判定等级（verified/inferred/unverified/conflict），显式记录 uncertainty/limitations/pending_human，材料缺失绝不补数。当需要整理实验材料成方法章节、参数表、证据映射或复现清单时使用。
version: 2.0.0
---

# 方法与实验章节写作 Skill（降级与不确定性强化版）

## 1. 角色与何时使用

本 Skill 把散落材料整理成论文 Methods/Experiments 章节，交付可逐条回溯的证据映射与诚实的边界标注。**核心四问**：写得全（六类要素齐）、写得准（数值与配置/日志/代码逐字一致，不补数）、做得着（版本/环境/种子/命令可复现）、说得住（缺口与不确定显式标出、限制指向具体失效场景）。

适用：方法步骤、实验设置、参数表、数据集与划分、对照实验、可复现与限制六类写作。不适用：一次性问答、纯润色、期刊排版、缺少任何科研材料的"凭空示例写作"。

**本版设计取舍**：以"判定等级 + 置信度 + 缺口登记"为第一公民，任何事实性论断必须落在一个判定等级上；不确定与限制不是附录套话，而是结果文件中的结构化字段。

## 2. 输入契约

- 运行时输入在 `/input/case.json` 与 `/input/task.md`；科研材料（PDF/代码/配置/日志/数据说明/笔记）也在 `/input/` 下，**按实际文件读取，不凭文件名或领域标签假设内容**。
- `case.json` 的 `output_contract` 是必须**原样回填**的字段清单（键名、数量一致，未知值给 `null`/`[]`/`{}`，不改名不删键）。缺省或不存在时脚本回退到标准字段并记录。
- 文本按 `utf-8-sig` 读取，容忍 BOM/坏编码/坏 JSON；单文件 2MB 上限，超限跳过并记入 `degradations`。
- 中间产物写 `/app/work/`，最终产物与 `result.json` 写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段：

- `case_id`：来自 `case.json` 的 case_id/id/challenge_id，缺省 `"unknown"`。
- `output_contract` 的**全部原始字段**（原样键名）。
- 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`artifacts`、`sources`、`uncertainty`、`limitations`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime`。
- `uncertainty` 为结构化对象：`summary`、`unverified_claims[]`（未证实 S#）、`conflicting_claims[]`（{ids, topic, values, sources}）、`low_confidence_claims[]`、`version_gaps[]`、`seed_gaps[]`。
- `limitations` 为数组，每条 `{id, scope, condition, failure_scenario, mitigation}`，**必须落到具体失效场景**（如"材料未报 GPU 型号 → 读者无法在相同硬件复现"），禁写"本文仍有局限"式套话。

主产物：可编辑成稿 `manuscript.md`；`evidence_map.json`（`[S#]` → locator + 逐字 quote + **verdict + confidence + uncertainty_note**）；`preserved_facts.json`（数值/单位/统计量逐条 quote）；`gap_register.json`（六类要素状态 + 缺口）。可编辑 Office/图产物同时给真实渲染 PDF/PNG 预览；`result.json` 的路径一律相对 `/app/output/`。

## 4. 判定等级与事实保全（本版核心）

每个关键结论/数值/图元在 `evidence_map.json` 中必须带四要素：

| verdict | 含义 | 何时使用 |
|---|---|---|
| `verified` | 材料逐字支持 | quote 与结论逐字一致 |
| `inferred` | 由材料合理推出 | 改写/概括，非逐字 |
| `unverified` | 无出处 | 疑似结论但无材料，标 `MATERIAL GAP` |
| `conflict` | 来源冲突 | 两个来源不一致，都记录并标 `source_conflict` |

`confidence` 取 `high | medium | low`；`uncertainty_note` 写清"不确定在哪、为什么"。数值逐字复用，不改单位/有效数字/统计量；限定词强度（"显著/可能"）不升不降。缺失写 `not_reported` 或 `MATERIAL GAP` 或 `pending_human`，**绝不补数、绝不虚构引用/DOI/数值**。冲突并列记录，不自行裁定。

## 5. 执行流程（总预算 900 秒、64 次模型请求、单回复含推理 16384 tokens）

**第 0 步（0–20 秒，脚本执行，不占请求）——先落盘骨架。前 3 分钟内必须完成；此后任何异常都不留不完整产物。**

```bash
python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work
```

它读 `case.json`/`task.md`，回填 `output_contract` 全字段（占位空值），写合法 `result.json` 骨架与 `/app/work/state.json`。

**第 1 步（0–60 秒，约 1–2 次请求）——发现材料。** 运行：

```bash
python3 scripts/main.py inventory --input /input --output /app/output --work /app/work
```

得到 `/app/work/materials_inventory.json`、`/app/work/extracted_facts.json`（带 locator + 逐字 quote 的**候选**事实）与 `/app/output/gap_register.json`（六类要素种子）。再读 `state.json`、`case.json`、`task.md` 全文，确认体裁、受众、格式与六类要素要求。

**第 2 步（60–300 秒，约 6–15 次请求）——建证据池。** 读 `extracted_facts.json` 与材料原文，**只采用带 locator 的候选，逐条对照原文核验**；把数值、版本、种子、数据集名、路径、命令归入六类要素；为每条核验结果打 verdict + confidence。不采信未核实的候选。

**第 3 步（300–600 秒，约 15–30 次请求）——写初稿。** 产出 `manuscript.md`，六类要素逐项覆盖；每个事实性句子挂 `[S#]`；同步写 `evidence_map.json`、`preserved_facts.json`，回填 `gap_register.json`（status 为 found/partial/not_found）。缺口立即写 `MATERIAL GAP` / `pending_human`，不阻塞成稿；把未证实/低置信/冲突逐条填入 `uncertainty` 与 `limitations`。

**第 4 步（600–840 秒，约 15–30 次请求）——增量改进。** 只做三件事：① 正文数值与参数表逐条对齐；② train/val/test 条件、种子、版本、命令逐条核对，消除条件串台与补数；③ 缺口追问补入 `pending_human`（每条含：缺什么 token、位置、为何阻塞）。每轮追加一条 `change_log`（不改写历史）。

**第 5 步（840–900 秒，脚本执行）——自检收口。** 运行：

```bash
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
```

按 `validation_report.json` 修正（缺字段/坏路径/缺证据/六类要素空）后重跑至通过；`status` 置 `ok`（成稿齐全、证据齐、六类要素非 not_found）或如实 `partial`/`insufficient_evidence`。最后 20 秒只落盘，不开新分析。

提交前在开发机运行 `python3 scripts/selftest.py`（空输入/坏 JSON/正常输入三场景），退出码 0 才算脚本就绪。

## 6. 六类要素覆盖要求（结果完整性）

1. **方法步骤**：每步写 输入→操作→输出→判断条件，不把多步压成一句。
2. **实验设置**：训练/验证/测试分开；硬件、软件版本、运行环境、随机种子、重复次数，避免条件串台。
3. **参数与来源**：参数表含名称、取值、单位、作用对象、来源；正文数值与表对齐。
4. **数据集与划分**：名称、版本、规模、预处理、划分比例、随机方式、各子集用途；版本不明写 `not_reported`。
5. **对照实验**：基线名称与版本、是否同预算、评价指标、统计方式，使比较成为可比结论而非断言。
6. **可复现与限制**：复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景与适用范围。

详见 `references/templates.md`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 六类要素全部覆盖，`gap_register.json` 无 not_found（结果完整性）。
- [ ] 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 locator+quote+verdict+confidence 非空（结果可核验性）。
- [ ] `output_contract` 全字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`、`unverified`，无静默编造。
- [ ] 冲突并列记录并标 `source_conflict`；`limitations` 每条指向具体失效场景。
- [ ] `uncertainty` 结构体四项齐全，与正文未证实/低置信/冲突逐条对应。
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`validate` 通过。
- [ ] `change_log` 完整记录 骨架→发现→初稿→改进→收口，可回退。

## 8. 降级策略（诚实优先）

- **缺材料/空输入**：`status=insufficient_evidence`，`result.json` 仍合法，`limitations` 列出缺口，不生成示例答案。
- **缺软件**（无 pdftotext/Office）：调用题面 pdf-text-tool / office-renderer / ocr-tool；仍失败则记入 `degradations`（"未提取，改由工具处理"），不影响其它材料。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料。
- **冲突/无来源参数**：标 `source_conflict` 或 `unverified` + `pending_human`，不自行裁定。
- **时间/请求耗尽**：先保 `result.json` 合法 + 可编辑初稿 + 证据映射，再按第 4 步增量完善；宁交诚实 `partial`，不交编造 `ok`。
- 每个 `pending_human` 条目固定四字段 `{id, missing_token, location, blocking_reason}`，使"交作者确认"可复核、可执行。

## 9. 脚本说明（相对路径，标准库，确定性）

- `scripts/main.py`：子命令 `skeleton`（立即落盘合法 partial 骨架）/ `inventory`（发现材料、抽候选事实、种子 gap 登记）/ `validate`（契约、证据、六类要素、产物路径校验，写 `validation_report.json`）/ `selftest`（三场景自检）/ `run`（skeleton→inventory）。
- `scripts/selftest.py`：独立自检入口，等价 `python3 scripts/main.py selftest`。
- `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`；均可用参数覆盖。

参考：`references/templates.md`（六要素覆盖表、参数表、证据映射、缺口登记模板）、`references/checklist.md`（降级判定表与失败模式清单）。
