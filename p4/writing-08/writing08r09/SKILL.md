---
name: methods-experiments-writing
description: 从代码、配置、日志与数据说明中抽取六类要素，写成可复现、可复核的方法与实验章节，并保留逐字来源、缺口标注与修改记录。当需要把散落实验材料整理成论文 Methods/Experiments 章节、参数表或复现清单时使用。
version: 1.1.0
---

# 方法与实验章节写作 Skill

## 1. 角色与何时使用

把零散实验材料（代码、配置文件、日志、数据说明、实验记录）整理成学术论文的 Methods/Experiments 章节，并交付可逐条回溯的证据映射与可编辑成稿。适用：方法步骤、实验设置、参数与来源、数据集与划分、对照实验、可复现与限制六类写作任务。不适用：一次性问答、纯润色、期刊排版。

核心原则：**写得全、写得准、做得着、说得住**。事实性论断必须有出处；材料没有的数值写 `not_reported` 或 `MATERIAL GAP`，绝不补数；冲突并列记录并标 `source_conflict`。

## 2. 输入契约

- 运行时输入在 `/input/case.json` 与 `/input/task.md`；科研材料（PDF/代码/配置/日志/数据说明/笔记）同在 `/input/` 下，按实际文件读取，不凭文件名或领域标签假设内容。
- `case.json` 中的 `output_contract` 是必须原样回填的字段清单（缺值给 `null`/`[]`/`{}`，不改名、不删未知键）；`task.md` 给出体裁、受众、格式约束与评分证据范围。
- 中间产物写 `/app/work/`，最终产物与 `result.json` 写 `/app/output/`。
- 文本按 `utf-8-sig` 优先读取，容忍 BOM/坏编码/坏 JSON；单文件 2MB 上限，超限跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段：

- `case_id`：来自 `case.json` 的 case_id/id/challenge_id，缺省 `"unknown"`。
- `output_contract` 的**全部原始字段**：键名、数量与原样一致。
- 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`artifacts`（相对 `/app/output/` 的产物路径列表）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`coverage`、`runtime`。
- 主产物：可编辑成稿 `manuscript.md`；配套 `evidence_map.json`（`[S#]` → locator + 逐字 quote）与 `preserved_facts.json`（数值/单位/统计量逐条 quote）。
- 可编辑 Office/图产物同时给真实渲染 PDF/PNG 预览；`result.json` 引用路径一律相对 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 0 步（0–20 秒，脚本执行，不占模型请求）——先落盘骨架。** 立即运行
`python3 scripts/main.py skeleton --input /input --output /app/output --work /app/work`，
回填 `output_contract` 全部字段（占位空值），写出合法 `result.json` 骨架与 `/app/work/state.json`。**前 3 分钟内必须完成此步**；此后任何异常都不会留下不完整产物。

**第 1 步（0–60 秒，约 1–2 次请求）——发现材料。** 运行
`python3 scripts/main.py inventory --input /input --output /app/output --work /app/work`，
得到 `/app/work/materials_inventory.json` 与 `extracted_facts.json`（带 locator + 逐字 quote 的候选事实）。再读 `state.json`、`case.json`、`task.md` 全文，确认体裁、受众、格式与六类要素要求。

**第 2 步（60–300 秒，约 6–15 次请求）——建证据池。** 读 `extracted_facts.json` 与材料原文，只采用带 locator 的候选；把数值、版本、种子、数据集名、路径、命令归入六类要素，逐条对照材料确认，不采信未经核实的候选。

**第 3 步（300–600 秒，约 15–30 次请求）——写初稿。** 产出 `manuscript.md`，按任务要求组织章节，六类要素逐项覆盖；每个事实性句子挂 `[S#]`，同步写 `evidence_map.json` 与 `preserved_facts.json`。缺口立即写 `MATERIAL GAP` / `pending_human`，不阻塞成稿。

**第 4 步（600–840 秒，约 15–30 次请求）——增量改进。** 只做三件事：① 正文数值与参数表逐条对齐；② train/val/test 条件、种子、版本、命令逐条核对，消除条件串台与补数；③ 缺口追问补入 `pending_human`。每轮改进都追加一条 `change_log`，不改写历史记录。

**第 5 步（840–900 秒，脚本执行）——自检收口。** 运行
`python3 scripts/main.py validate --input /input --output /app/output --work /app/work`，
按 `validation_report.json` 修正 `result.json`（缺字段/坏路径/缺证据）后重跑至通过；更新 `status` 为 `ok`（成稿齐全）或如实 `partial`/`insufficient_evidence`。最后 20 秒只做落盘，不再开新分析。

## 5. 六类要素覆盖

1. **方法步骤**：每步写 输入→操作→输出→判断条件，不把多步压成一句。
2. **实验设置**：训练/验证/测试分开；写硬件、软件版本、运行环境、随机种子、重复次数，避免条件串台。
3. **参数与来源**：参数表含名称、取值、单位、作用对象、来源；正文数值与表对齐。
4. **数据集与划分**：名称、版本、规模、预处理、划分比例、随机方式、各子集用途；版本不明写 `not_reported`。
5. **对照实验**：基线名称与版本、是否同预算、评价指标、统计方式，使比较成为可比结论而非断言。
6. **可复现与限制**：复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景与适用范围。

模板见 `references/templates.md`，检查清单见 `references/checklist.md`。

## 6. 证据与事实保全

- 每个关键结论/数值/图元都要能指回材料逐字片段（locator + quote）；locator 形如 `materials/<file>#L<line>` 或 `paper.pdf#p<page>`。
- 数值逐字复用，不改单位、有效数字、统计量（mean±sd、n、p 值）；限定词强度（"显著/可能"）不升不降。
- 冲突信息不隐藏：同一指标两个来源不一致时两者都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 六类要素全部覆盖，无空节或占位空话（结果完整性）。
- [ ] 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 locator+quote 非空（结果可核验性）。
- [ ] `output_contract` 全部字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`，无静默编造。
- [ ] 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景而非套话。
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`validate` 通过。
- [ ] `change_log` 完整记录骨架→初稿→改进→收口，可回退。

## 8. 降级策略（诚实优先）

- **缺材料/空输入**：`status=insufficient_evidence`，`result.json` 仍合法，列出缺口，不生成示例答案。
- **缺软件**（如无 pdftotext/Office）：调用题面提供的 pdf-text-tool / office-renderer / ocr-tool；仍失败则在 `degradations` 记录"未提取，改由工具处理"，不影响其它材料。
- **坏 JSON/坏编码**：记录到 `errors`/`degradations`，继续处理其余材料。
- **冲突/无来源参数**：标 `source_conflict` 或 `pending_human`，不自行裁定。
- **时间或请求耗尽**：先保证 `result.json` 合法 + 可编辑初稿 + 证据映射，再按第 4 步增量完善；宁交诚实的 `partial`，不交编造的 `ok`。

## 9. 脚本与本地自检

- `scripts/main.py`：`skeleton | inventory | validate` 三个确定性阶段，标准库实现；`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。
- `scripts/selftest.py`：空输入、坏 JSON、最小样例三种场景自检，退出码 0 即通过；提交前运行 `python3 scripts/selftest.py`。
