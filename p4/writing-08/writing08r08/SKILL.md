---
name: methods-experiments-writing
description: 把零散的代码、配置、日志与数据说明抽取成六类要素，写成可复现、可复核的方法与实验章节；为每个事实保留 locator+逐字 quote，显式标注缺口、冲突与待人工确认项，并把题目五项评分维度逐条映射到执行步骤与自检项。当需要将散落实验材料整理成论文 Methods/Experiments 章节、参数表或复现清单时使用。
version: 2.0.0
---

# 方法与实验章节写作 Skill

## 1. 角色与何时使用

本 Skill 把零散实验材料（代码、配置文件、日志、数据说明、实验记录）整理成学术论文的 Methods/Experiments 章节，交付可逐条回溯的证据映射。覆盖六类要素：方法步骤、实验设置、参数与来源、数据集与划分、对照实验、可复现与限制。不适用：一次性问答、纯润色、期刊排版。

核心原则：**写得全、写得准、做得着、说得住**。事实性论断必须有出处；材料没有的数值写 `not_reported` 或标 `MATERIAL GAP`，绝不补数；冲突并列记录并标 `source_conflict`。

## 2. 输入契约

- 运行时输入在 `/input/case.json` 与 `/input/task.md`；科研材料（PDF/代码/配置/日志/数据说明/笔记）也在 `/input/` 下，按实际文件读取，不凭文件名或领域标签假设内容。
- `case.json` 中的 `output_contract` 是必须回填的字段清单（键名、数量与原样一致）；`task.md` 给出体裁、受众、格式约束与评分证据范围。
- 文本按 `utf-8-sig` 优先读取，容忍 BOM/坏编码/坏 JSON；单文件 2MB 上限，超限跳过并记录。
- 中间产物写 `/app/work/`，最终产物与 `result.json` 写 `/app/output/`。
- 脚本相对路径以本 Skill 根目录（运行时为 `/skills/weekly-skill`）为基准。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层字段为：

- `case_id`：来自 `case.json` 的 `case_id`/`id`/`challenge_id`，缺省 `"unknown"`。
- `output_contract` 的**全部原始字段**：键名、数量与原样一致，未知值给 `null`/`[]`/`{}`，不改名、不删未知键。
- 标准字段：`status`（`ok | partial | insufficient_evidence | input_error`）、`artifacts`（可编辑产物路径列表）、`sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`limitations`、`pending_human`、`runtime`。
- 主产物：可编辑成稿 `manuscript.md`；配套 `evidence_map.json`（`[S#]` → locator + 逐字 quote）与 `preserved_facts.json`（数值/单位/统计量逐条 quote）。
- 可编辑 Office/图产物同时给真实渲染 PDF/PNG 预览；`result.json` 引用路径一律相对 `/app/output/`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

**第 0 步（0–20 秒，脚本执行，不占模型请求）——先落盘骨架。** 立即运行：

```bash
python3 scripts/skeleton.py --input /input --output /app/output --work /app/work
```

它读取 `case.json`/`task.md`，回填 `output_contract` 全部字段（占位空值），写出合法 `result.json` 骨架与 `/app/work/state.json`。**前 3 分钟内必须完成此步**；此后任何异常都不会留下不完整产物。

**第 1 步（0–60 秒，约 1–2 次请求）——发现材料。** 运行 `python3 scripts/inventory.py --input /input --output /app/output --work /app/work`，得到 `/app/work/materials_inventory.json` 与 `extracted_facts.json`（带 locator + 逐字 quote 的候选事实）。再读 `state.json`、`case.json`、`task.md` 全文，确认体裁、受众、格式、六类要素要求。

**第 2 步（60–300 秒，约 6–15 次请求）——建证据池。** 读 `extracted_facts.json` 与材料原文，只采用带 locator 的候选；把数值、版本、种子、数据集名、路径、命令归类到六类要素，逐条对照材料确认，不采信未经核实的候选；冲突候选记入 `source_conflict`。

**第 3 步（300–600 秒，约 15–30 次请求）——写初稿。** 产出 `manuscript.md`，按任务要求组织章节，六类要素逐项覆盖（见第 5 节）；每个事实性句子挂 `[S#]`，同步写 `evidence_map.json` 与 `preserved_facts.json`。缺口立即写 `MATERIAL GAP` / `pending_human`，不阻塞成稿。

**第 4 步（600–840 秒，约 15–30 次请求）——增量改进。** 只做三件事：① 正文数值与参数表逐条对齐；② train/val/test 条件、种子、版本、命令逐条核对，消除条件串台与补数；③ 缺口追问补入 `pending_human`。每轮改进都追加一条 `change_log`，不改写历史记录。

**第 5 步（840–900 秒，脚本执行）——自检收口。** 运行 `python3 scripts/validate.py --input /input --output /app/output --work /app/work`，按报告修正 `result.json`（缺字段/坏路径/缺证据）后重跑至通过；更新 `status` 为 `ok`（成稿齐全）或如实 `partial`/`insufficient_evidence`。最后 20 秒只做落盘，不再开新分析。

## 5. 六类要素覆盖要求（对应“结果完整性”评分点）

1. **方法步骤**：每步写 输入→操作→输出→判断条件，不把多步压成一句。
2. **实验设置**：训练/验证/测试分开；写硬件、软件版本、运行环境、随机种子、重复次数，避免条件串台。
3. **参数与来源**：参数表含名称、取值、单位、作用对象、来源；正文数值与表对齐。
4. **数据集与划分**：名称、版本、规模、预处理、划分比例、随机方式、各子集用途；版本不明写 `not_reported`。
5. **对照实验**：基线名称与版本、是否同预算、评价指标、统计方式，使比较成为可比结论而非断言。
6. **可复现与限制**：复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景与适用范围。

详见 `references/templates.md` 的六要素覆盖表、参数表与证据映射模板。

## 6. 证据与事实保全规则

- 每个关键结论/数值/图元都要能指回材料逐字片段（locator + quote）；locator 形如 `materials/<file>#L<line>` 或 `paper.pdf#p<page>`。
- 数值逐字复用，不改单位、有效数字、统计量（mean±sd、n、p 值）；限定词强度（"显著/可能"）不升不降。
- 冲突信息不隐藏：同一指标两个来源不一致时两者都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。

## 7. 评分维度对齐（本版核心设计）

把题目五项评分维度逐条映射到执行动作与自检项，使每一步都服务于明确得分点；完整映射见 `references/scoring_map.md`。

| 评分维度 | 分值 | 落到的执行动作 | 对应自检项 |
|-|-|-|-|
| 实际效果·任务完成度 | 15 | 第 1 步读体裁/受众/格式，第 3 步交付可用成稿 | 自检 1、2 |
| 实际效果·结果正确性 | 15 | 第 2/4 步逐条核对数值与条件，禁补数 | 自检 3、4 |
| 实际效果·结果完整性 | 5 | 第 3 步六类要素逐项覆盖 | 自检 1、5 |
| 实际效果·结果可核验性 | 5 | 第 3 步每句挂 `[S#]` 并写 evidence_map | 自检 6 |
| 使用增益·质量/效率 | 15+5 | 参数表+复现清单+追问清单，减少翻配置与返工 | 自检 5、7 |
| 使用增益·增益可验证 | 5 | 诚实 partial + change_log 回退，可对照基线与白板 | 自检 8、9 |
| 创新增益·新增能力/方法改进 | 5+5 | 抽取→对齐→缺口追问流水线，条件校验防串台 | 自检 10、11 |
| 创新增益·创新证据 | 5 | 边界写清，与填空式模板的区别说明 | 自检 12 |
| 可靠体验·调用/稳定/异常 | 3+4+3 | 相对路径、确定性、空/坏输入降级 | 自检 13、14 |
| 生态贡献·文档/复用/分享 | 3+4+3 | 模板、失败模式、维护信息可复用 | 自检 15、16 |

## 8. 自检清单（逐条勾选，对应评分维度）

- [ ] 1. 六类要素全部覆盖，无空节或占位空话（结果完整性）。
- [ ] 2. 满足 `task.md` 指定体裁、受众与格式约束（任务完成度）。
- [ ] 3. 正文每个数值/参数/版本与材料逐字一致，无补数（结果正确性）。
- [ ] 4. train/val/test 条件、种子、版本、命令分别核对，无串台（结果正确性）。
- [ ] 5. 参数表、复现清单、追问清单齐备（完整性+效率增益）。
- [ ] 6. 每个事实性句子有 `[S#]`，`evidence_map.json` 齐且 locator+quote 非空（可核验性）。
- [ ] 7. 正文数值与参数表逐条对齐（质量增益）。
- [ ] 8. `output_contract` 全部字段原样回填，`case_id`/`status`/`sources`/`uncertainty`/`change_log` 齐（输出契约）。
- [ ] 9. `change_log` 完整记录 骨架→初稿→改进→收口，可回退（增益可验证性）。
- [ ] 10. 缺口显式标注：`MATERIAL GAP`、`pending_human`、`not_reported`，无静默编造（异常处理）。
- [ ] 11. 冲突并列记录并标 `source_conflict`；限制条件指向具体失效场景而非套话（方法改进）。
- [ ] 12. 边界与适用条件写清，说明相对填空式模板的差别（创新证据）。
- [ ] 13. 脚本用相对路径、只依赖标准库，重复运行结果一致（运行稳定性）。
- [ ] 14. 空输入/坏 JSON/缺材料三种情况不崩溃，均留合法产物（异常处理）。
- [ ] 15. 模板、失败模式、维护信息齐全，他人可复用（生态贡献）。
- [ ] 16. 可编辑主产物 + 渲染 PDF/PNG 预览均存在；`validate.py` 通过（交付标准）。

## 9. 降级策略（诚实优先）

- **缺材料/空输入**：`status=insufficient_evidence`，`result.json` 仍合法，列出缺口，不生成示例答案。
- **缺软件**（如无 pdftotext/Office）：调用题面提供的 pdf-text-tool / office-renderer / ocr-tool；仍失败则在 `degradations` 记录"未提取，改由工具处理"，不影响其它材料。
- **坏 JSON/坏编码**：记录到 `errors`/`degradations`，继续处理其余材料。
- **冲突/无来源参数**：标 `source_conflict` 或 `pending_human`，不自行裁定。
- **时间或请求耗尽**：先保证 `result.json` 合法 + 可编辑初稿 + 证据映射，再按第 4 步增量完善；宁交诚实的 `partial`，不交编造的 `ok`。

## 10. 脚本说明（相对路径，标准库）

- `scripts/skeleton.py`：读 `case.json`/`task.md`，立即写合法 `result.json` 骨架与 `state.json`。
- `scripts/inventory.py`：扫描 `/input`，分类文件，抽取候选事实（数值+单位/版本/种子/超参/数据集/路径/命令），PDF 与 Office 尽力抽取文本。
- `scripts/validate.py`：校验 `result.json` 的契约字段、状态、证据与产物路径，输出 `validation_report.json`。
- `scripts/selftest.py`：交付前离线自检（空输入/坏 JSON/最小案例/重复运行一致性）。

四个脚本均 `python3` 标准库可运行、确定性执行；`--input/--output/--work` 缺省为 `/input`、`/app/output`、`/app/work`。
