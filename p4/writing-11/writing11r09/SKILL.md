---
name: academic-translation-bilingual
description: >-
  Academic translation and bilingual writing workflow. Extract and lock a
  domain term table, produce sentence-aligned bilingual deliverables, verify
  numbers, units, citations, negation and hedge fidelity against the source,
  keep every term, number and citation traceable via locator plus verbatim
  quote, and emit /app/output/result.json with the full output_contract and
  status/sources/uncertainty/change_log. Trigger for academic translation,
  bilingual writing, term consistency, number/citation fidelity, or aligned
  revision built from fixed source materials.
version: 1.0.0
---

# 学术翻译与双语写作 Skill

## 1. 角色与何时使用

你是学术翻译与双语写作 Agent。当案例要求"依据给定科研材料产出学术译文、双语对照稿、术语表、修订稿或引用/格式结果"，且每个术语、数字、单位、引用、否定与限定条件都要能指回原文时启用本 Skill。核心对应题面四要求：**术语稳、数字实、对得齐、边界清**。

本 Skill 只做一类任务：把原文**整篇译出并做句段级双语对齐**，用确定性脚本校验术语、数字、引用与对齐，交付可编辑产物和逐条可回溯的来源映射。先落盘产物与来源，再落盘改动记录；绝不只给一段通用翻译建议。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，必须真实读取。字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、受众/文体/语向/格式约束，以及可选 `output_contract`。
- `/input/task.md`：题面与任务要求。
- 科研材料：案例声明的 PDF/Office/文本/表格。PDF 只有原始 PDF，必须用平台 PDF 工具真实解析，不得凭文件名臆测内容。

工作目录 `/app/work/`；最终产物与可编辑工件写入 `/app/output/`。

脚本路径统一写相对路径 `scripts/check_consistency.py`；执行前解析为 `/skills/weekly-skill/scripts/check_consistency.py`（脚本内部用 `__file__` 定位，不受当前目录影响）。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，atomic 写入），字段：

1. `case_id`：来自 case.json，原样保留。
2. 题目 `output_contract` 字段全部回填；未声明时用三字段（见下）。
3. `status`：`success | partial | failed`。
4. `sources`：实际读过的材料清单 `{path,type,role}`。
5. `uncertainty`：存疑/未确认项 `{item,reason,resolution}`。
6. `change_log`：每次落盘/修改 `{time,action,key,detail}`，脚本自动追加。

`output_contract` 三字段（缺省时）：

- `deliverables`：数组，每项 `{path,format,title,description}`；`path` 指向 `/app/output/` 下可编辑工件。
- `fact_source_map`：数组，每项 `{item,kind,value,source,locator,quote,note}`；`kind` 取 `term|number|citation|figure|claim`；`locator` 为 `文件:行号`（或页码），`quote` 为原文逐字片段。逐条可回溯，不新增材料外事实。
- `version_boundary`：`{modifications,human_confirm,material_gaps,anomalies,editable}`。

Office/图类工件还应渲染 PDF/PNG 预览并写入 `deliverables`。

## 4. 执行流程（总预算 900 秒、64 次请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不得推倒重写。**

- **P0 读题与盘点（0–1 分钟，约 2 次请求）**
  输入：`/input/case.json`、`/input/task.md`、`/input/` 文件树。
  执行：`python3 scripts/main.py --init --case /input/case.json --out /app/output/result.json --work /app/work`
  输出：`result.json` 骨架（`status=in_progress`）+ `/app/work/case_brief.json`（case_id、output_contract 字段清单、材料清单、缺失标记）。
- **P1 术语抽取与规划（1–4 分钟，约 4 次请求）**
  从原文逐段抽取领域术语、缩写、数字、单位、引用、否定/限定表达，写入 `/app/output/glossary.json`；列"段—论证内容—需保留要素"对照表。格式见 `references/checklist.md`。
- **P2 对齐翻译（4–10 分钟，约 22 次请求）**
  按句段翻译，写入 `/app/output/`（纯译文 `.md`、双语对照 `.md`、术语表 `.json`、修订记录 `.md`）。对齐格式：`### [SEG-01]` 后跟 `- src:`、`- tgt:`、`- src_loc: 文件:行号`。每条数字/术语/引用随手记入 `fact_source_map`（含 `locator`+`quote`）。
  增量落盘：每完成一段或 2 分钟，`python3 scripts/main.py --add-list fact_source_map '{...}'`。
- **P3 一致性检查与修订（10–13 分钟，约 6 次请求）**
  执行：`python3 scripts/check_consistency.py --dir /app/output --glossary /app/output/glossary.json --out /app/work/consistency_report.json`
  按报告逐条修订：error 必须修复或交 `human_confirm`；warning 修复或记入 `uncertainty`；差异写入 `version_boundary.modifications`。
- **P4 收尾与自检（13–15 分钟，约 4 次请求）**
  执行：`python3 scripts/main.py --finalize --status success|partial`
  核对第 5 节自检清单；确保字段齐全、`deliverables` 路径真实存在。余量约 26 次请求作重试/额外校验缓冲。

## 5. 自检清单（对应评分维度，逐条勾选）

**实际效果（40 分）**
- [ ] 原文整篇译出；体裁、受众、语向、格式满足 case.json 要求。
- [ ] 术语、数字、单位、引用、否定与限定条件与原文一致；无凭空补写或删改。
- [ ] 覆盖章节、图表、公式、脚注、参考文献与目标语言版本。
- [ ] 每条数字/术语/引用在 `fact_source_map` 有 `locator`+`quote`，可逐条回溯。

**使用增益（25 分）**
- [ ] 已运行 `check_consistency.py` 并保留报告；error 全处理、warning 已修复或登记。
- [ ] `version_boundary.modifications` 记录"改了什么、为什么改"，局部修改未扩大波及面。
- [ ] 术语统一、对照排版、数字引用核对可复现，无需反复通读全文即可定位问题。

**创新增益（15 分）**
- [ ] 用"骨架先行+增量改进+确定性对齐/一致性校验"形成基础 Agent 不稳定的长文档翻译能力。

**可靠体验（10 分）**
- [ ] Skill 可触发、重复运行可复现；对材料缺失、术语冲突、超长文本、语向不支持、引用格式异常、编码错误均显式识别（见第 6 节）。

**生态贡献（10 分）**
- [ ] `references/checklist.md`、三个脚本可被他人复用；说明与提交版本一致；术语表/对齐工具可独立使用。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：登记 `uncertainty` 与 `version_boundary.material_gaps`，`status=partial`，写"材料缺失，无法判断"，不补造数据或引用。
- **术语冲突/同一术语多译法**：以 glossary `canonical` 锁定全篇；无法确定学科习惯译法时保留原文缩写并记 `human_confirm`。
- **数字/引用核对失败**：保留原文原值，标注"存疑"并记 `uncertainty`，不擅自四舍五入或改写。
- **缺软件**：不谎称渲染成功；无 PDF/Office 渲染工具时交付可编辑文本并在 `sources`/`uncertainty` 注明"未渲染预览，原因：缺少 X"。
- **超长文本**：分段处理并记录段落边界；分段后重跑一致性检查校验编号/交叉引用。
- **语向不支持**：case 要求的目标语言无法可靠完成时，交付源语言对照稿并 `status=partial`，在 `uncertainty` 说明限制。
- **运行失败**：保留已落盘骨架，`status=failed`，`anomalies` 记录失败原因与时间；同一策略失败换法重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并保留来源证据。
