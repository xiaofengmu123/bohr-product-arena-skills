---
name: methods-experiments-writing
description: 从代码、配置、日志与数据说明中抽取六类要素，写成可复现、可复核的方法与实验章节，逐字段对齐 output_contract，保留逐字来源、缺口标注与追加式修改记录。当需要把散落实验材料整理成论文 Methods/Experiments 章节、参数表或复现清单时使用。
version: 2.0.0
---

# 方法与实验章节写作 Skill（契约优先版）

## 1. 角色与何时使用

本 Skill 把零散实验材料（代码、配置文件、日志、数据说明、实验记录）整理成学术
Methods/Experiments 章节，并交付可逐条回溯的证据映射与逐字段对齐的 `result.json`。
适用于六类写作任务：方法步骤、实验设置、参数与来源、数据集与划分、对照实验、
可复现性与限制。不适用：一次性问答、纯润色、期刊排版。

核心原则：**写得全、写得准、做得着、说得住**。事实性论断必须有出处；材料没有的
数值写 `not_reported` 或 `MATERIAL GAP`，绝不补数；冲突并列记录并标 `source_conflict`。

本版设计取舍（相对上一版）：**输出契约优先（contract-first）**。骨架阶段先把
`output_contract` 的每个字段按类型回填占位值并落盘，此后所有改进都在该合法骨架上
增量追加，`change_log` 只增不改；`validate` 逐字段审计契约对齐。

## 2. 输入契约

- 运行时输入在 `/input/case.json` 与 `/input/task.md`；科研材料（PDF/代码/配置/日志/
  数据说明/笔记）也在 `/input/` 下，按实际文件读取，不凭文件名或领域标签假设内容。
- `case.json` 中的 `output_contract` 是必须原样回填的字段清单；`task.md` 给出体裁、
  受众、格式约束与评分证据范围。
- 中间产物写 `/app/work/`，最终产物与 `result.json` 写 `/app/output/`。
- 文本按 `utf-8-sig` 优先读取，容忍 BOM/坏编码/坏 JSON；单文件 2MB 上限，超限跳过并记录。
- 脚本 `--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。

## 3. 输出契约（契约优先）

必须产出 `/app/output/result.json`，顶层字段为：

- `case_id`：来自 `case.json` 的 case_id/id/challenge_id，缺省 `"unknown"`。
- **`output_contract` 的全部原始字段**：键名、数量与原样一致，未知值给 `null/[]/{}`，
  不改名、不删未知键。字段的占位类型跟随契约 schema（string→`""`、array→`[]`、
  object→`{}`、number→`null`）。
- 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`）、
  `artifacts`（可编辑产物路径列表）、`sources`、`uncertainty`、`change_log`、
  `errors`、`degradations`、`limitations`、`pending_human`、`runtime`。
- `change_log` 为**追加式**结构化记录（`seq` 严格递增），只增不改，可回退。
- 主产物：可编辑成稿 `manuscript.md`；配套 `evidence_map.json`（`[S#]` → locator +
  逐字 quote）与 `preserved_facts.json`（数值/单位/统计量逐条 quote）。
- 可编辑 Office/图产物同时给真实渲染 PDF/PNG 预览；`result.json` 引用路径一律相对
  `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 0 步（0–20 秒，脚本执行，不占模型请求）——先落盘契约骨架。**
立即运行 `python3 scripts/main.py skeleton`，读取 `case.json`/`task.md`，把
`output_contract` 全部字段按类型回填占位值，写出合法 `result.json` 骨架与
`/app/work/state.json`。**前 3 分钟内必须完成此步**；此后任何异常都不会留下不完整产物。

**第 1 步（0–60 秒，约 1–2 次请求）——发现材料。** 运行
`python3 scripts/main.py inventory`，得到 `/app/work/materials_inventory.json` 与
`extracted_facts.json`（带 locator + 逐字 quote 的候选事实）。再读 `state.json`、
`case.json`、`task.md` 全文，确认体裁、受众、格式与六类要素要求。

**第 2 步（60–300 秒，约 6–15 次请求）——建证据池。** 读 `extracted_facts.json` 与
材料原文，只采用带 locator 的候选；把数值、版本、种子、数据集名、路径、命令归类到
六类要素，逐条对照材料确认，不采信未经核实的候选。

**第 3 步（300–600 秒，约 15–30 次请求）——写初稿。** 产出 `manuscript.md`，按任务
要求组织章节，六类要素逐项覆盖（见第 5 节）；每个事实性句子挂 `[S#]`，同步写
`evidence_map.json` 与 `preserved_facts.json`。缺口立即写 `MATERIAL GAP` /
`pending_human`，不阻塞成稿；回填 `output_contract` 各字段真实值，追加 `change_log`。

**第 4 步（600–840 秒，约 15–30 次请求）——增量改进。** 只做三件事：① 正文数值与
参数表逐条对齐；② train/val/test 条件、种子、版本、命令逐条核对，消除条件串台与
补数；③ 缺口追问补入 `pending_human`。每轮改进追加一条 `change_log`，不改写历史。

**第 5 步（840–900 秒，脚本执行）——自检收口。** 运行 `python3 scripts/main.py
validate`，按报告修正 `result.json`（缺字段/坏路径/缺证据）后重跑至通过；更新
`status` 为 `ok`（成稿齐全）或如实 `partial`/`insufficient_evidence`。最后 20 秒只做
落盘，不再开新分析。

## 5. 六类要素覆盖要求（结果完整性评分点）

1. **方法步骤**：每步写 输入→操作→输出→判断条件，不把多步压成一句。
2. **实验设置**：训练/验证/测试分开；写硬件、软件版本、运行环境、随机种子、重复次数。
3. **参数与来源**：参数表含名称、取值、单位、作用对象、来源；正文数值与表对齐。
4. **数据集与划分**：名称、版本、规模、预处理、划分比例、随机方式、各子集用途。
5. **对照实验**：基线名称与版本、是否同预算、评价指标、统计方式，使比较成为可比结论。
6. **可复现与限制**：复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景。

详见 `references/templates.md` 的六要素覆盖表、参数表与证据映射模板。

## 6. 证据与事实保全规则

- 每个关键结论/数值/图元都要能指回材料逐字片段（locator + quote）；locator 形如
  `materials/<file>#L<line>` 或 `materials/<pdf>#p<page>`。
- 数值逐字复用，不改单位、有效数字、统计量（mean±sd、n、p 值）；限定词强度不升不降。
- 冲突信息不隐藏：同一指标两个来源不一致时两者都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 7. 自检清单（逐条勾选，对应评分维度）

- [ ] 六类要素全部覆盖，无空节或占位空话（结果完整性）。
- [ ] 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性）。
- [ ] 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 locator+quote 非空（可核验性）。
- [ ] `output_contract` 全部字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/
  `change_log` 齐（输出契约）。
- [ ] 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`，无静默编造。
- [ ] 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景而非套话。
- [ ] 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`python3 scripts/main.py validate` 通过。
- [ ] `change_log` 完整记录骨架→初稿→改进→收口，`seq` 递增，可回退。

## 8. 降级策略（诚实优先）

- **缺材料/空输入**：`status=insufficient_evidence`，`result.json` 仍合法，列缺口，不生成示例答案。
- **缺软件**（无 pdftotext/Office）：调用题面提供的 pdf-text-tool / office-renderer /
  ocr-tool；仍失败则在 `degradations` 记录"未提取，改由工具处理"，不影响其它材料。
- **坏 JSON/坏编码**：记录到 `errors`/`degradations`，继续处理其余材料。
- **冲突/无来源参数**：标 `source_conflict` 或 `pending_human`，不自行裁定。
- **时间或请求耗尽**：先保证 `result.json` 合法 + 可编辑初稿 + 证据映射，再按第 4 步
  增量完善；宁交诚实的 `partial`，不交编造的 `ok`。

## 9. 脚本说明（相对路径，标准库）

- `scripts/main.py skeleton`：读 `case.json`/`task.md`，立即写合法 `result.json` 骨架与
  `state.json`；契约字段按 schema 类型回填占位值。
- `scripts/main.py inventory`：扫描 `/input`，分类文件，抽取候选事实（数值+单位/版本/
  种子/超参/数据集/路径/命令），PDF 尽力抽取文本。
- `scripts/main.py validate`：逐字段校验 `result.json` 契约对齐、状态、证据与产物路径，
  输出 `validation_report.json`。
- `scripts/main.py selftest`：空输入、坏 JSON、契约回填三种情况各跑一次，退出码 0 表示通过。

四个子命令均 `python3` 标准库可运行、确定性执行。
