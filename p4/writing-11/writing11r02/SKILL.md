---
name: academic-translation-bilingual
description: >-
  Academic translation and bilingual writing workflow. Detect the source and
  target language, extract a term glossary and lock one translation across
  abstract, body, captions and appendix, preserve numbers, units, citations,
  negation scope and hedges verbatim, emit segment-aligned editable bilingual
  drafts, verify term-number-citation-alignment consistency deterministically,
  and write /app/output/result.json with case_id, all output_contract fields,
  status, sources, uncertainty, pending_human and change_log. Trigger when a
  case asks to translate or bilingual-write academic or scientific material
  from fixed source documents.
version: 1.1.0
---

# 学术翻译与双语写作 Skill（academic-translation-bilingual）

## 1. 角色与何时使用

本 Skill 面向"依据固定科研材料完成学术翻译或双语写作"的任务：给定原文（PDF/Office/文本），产出可编辑的译文或双语对照稿件，并对术语、数字、引用、否定与限定条件逐条溯源。核心目标对应题目四问：**术语稳、数字实、对得齐、边界清**。

启用条件（满足任一）：要求把学术/科研材料译成目标语言；产出双语对照稿件；改写/校对译文；统一术语译法；核对数字、单位、引用、脚注、参考文献。不适用：纯聊天问答、无源材料的一次性造句。

## 2. 输入契约

- `/input/case.json`：案例定义，必须真实读取。字段可能含 `case_id`/`id`/`caseId`、任务说明、材料路径、语向（`source_language`/`target_language`）、体裁/受众/格式约束，以及可选 `output_contract`。
- `/input/task.md`：题面/约束（可能与 case.json 重复，以 case.json 的 output_contract 为准）。
- 科研材料：`/input/` 下声明的 PDF/Office/文本/表格。**PDF 只提供原始 PDF，无预提取层**，必须用平台 PDF 工具真实解析正文，不得凭文件名臆测内容。
- `organizer-edit-workorder.json`（如存在）：声明离线可编辑工作范围。

工作目录 `/app/work/`；最终产物与可编辑工件写 `/app/output/`。

**脚本路径**：脚本位于 Skill 根目录 `scripts/`，命令写相对路径 `scripts/<x>.py`。若当前工作目录不是 Skill 根目录（运行时通常是 `/app/work`），先把 `scripts/` 解析为 Skill 根目录（默认 `/skills/weekly-skill`）下的绝对路径再执行。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8，atomic 写入），字段：

1. `case_id`：来自 `/input/case.json`，原样保留。
2. 题目 `output_contract` 字段全部原样包含（按声明键名）；未声明时用三字段：
   - `deliverables`：`[{path, format, title, description, language, segments}]`，`path` 指向 `/app/output/` 下的可编辑工件（Markdown/LaTeX/Office）。
   - `fact_source_map`：`[{item, kind, value, locator, quote, note}]`；`kind` 取 `term|number|unit|citation|reference_entry|claim|figure|table|formula|footnote`；`locator` 为 `文件:页码/段落`，`quote` 为逐字片段，逐条可回溯，不新增材料外事实。
   - `version_boundary`：`{modifications, human_confirm, material_gaps, anomalies, editable}`。
3. `status`：`in_progress | success | partial | failed`（未完成必须 partial/failed）。
4. `sources`：`[{path, type, role}]` 实际读过并使用的输入材料。
5. `uncertainty`：`[{item, reason, resolution}]`。
6. `pending_human`：`[{item, reason, suggested_action}]` 需人工确认项。
7. `change_log`：`[{time, action, key, detail}]`，由脚本自动追加。
8. `runtime`：`{start_epoch, end_epoch, seconds}`；`degradations`：`[string]` 降级记录。

Office/图/幻灯片类产物须另渲染真实 PDF/PNG 预览，预览路径写入 `deliverables`，便于复核。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬规则：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不得推倒重写。** 阶段预算：

- **P0 读题与盘点（0–1 分钟，约 2 请求）**：读 case.json/task.md、列 `/input/` 文件树；运行 `scripts/main.py brief` 生成 `/app/work/case_brief.json`（case_id、语向、output_contract 键、材料清单、缺失标记）。
- **P1 落盘骨架（1–3 分钟，约 2 请求）**：运行 `scripts/main.py init` 写 result.json（含 case_id + 全部 output_contract 字段空骨架，`status=in_progress`，change_log 首条 `init`）。**此步为最低保障产物，务必完成。**
- **P2 读材料+术语表+翻译（3–12 分钟，约 28 请求）**：真实解析原文；抽取术语/缩写建 `glossary.json` 锁定译法；按句段翻译产出双语对照工件；每条术语/数字/引用/论断随手记入 `fact_source_map`。每完成一节或 2 分钟用 `--set`/`--add-list`/`--add-note` 增量更新 result.json。
- **P3 一致性检查与修订（12–14 分钟，约 6 请求）**：运行 `scripts/check.py --dir /app/output --glossary /app/output/glossary.json --out /app/work/check_report.json`；按报告逐条修订，差异记入 `version_boundary.modifications`；无法确认者记 `uncertainty`/`pending_human`。
- **P4 收尾与自检（14–15 分钟，约 4 请求）**：运行 `scripts/main.py finalize --status <success|partial>`；逐条核对第 5 节清单；确认 result.json 字段齐全、`deliverables` 路径真实存在。余量约 22 请求作失败重试缓冲。

| 阶段 | 输入 | 输出 |
|---|---|---|
| P0 | /input/case.json + task.md + 文件树 | /app/work/case_brief.json |
| P1 | case_brief.json | /app/output/result.json 骨架 |
| P2 | 原文材料 | 双语稿 + glossary.json + fact_source_map |
| P3 | 稿件 + glossary.json | check_report.json + 修订 |
| P4 | 全部产物 | result.json（finalize） |

## 5. 自检清单（对应评分维度，逐条勾选）

**实际效果（40）**
- [ ] 整篇译出并按要求的输出形态（纯译文/双语对照/带修订标记）交付，可直接进入作者修改流程。
- [ ] 术语、数字、单位、引用、否定与限定条件与原文一致；无凭空补写或删改科学判断。
- [ ] 覆盖原文章节、图表、公式、脚注、参考文献，且产出目标语言版本。
- [ ] 每句段可追溯原文（locator+quote）、术语依据、数字/引用核对结果与修改痕迹。

**使用增益（25）**
- [ ] 已运行 `check.py` 并保留报告；术语/编号/引用/对齐问题已修复或登记。
- [ ] `version_boundary.modifications` 记录"改了什么/为什么/前后差异"，局部改动未扩大波及。
- [ ] 无需反复通读全文即可定位受影响句段（依据报告中的 locator/文件:行号）。

**创新增益（15）**
- [ ] 采用"术语表锁定 + 句段对齐 + 数字/引用/对齐三查"，形成基础 Agent 不易稳定的能力。
- [ ] 骨架先行 + 增量改进；脚本可独立复现核对，且与提交版本一致。

**可靠体验（10）**
- [ ] Skill 按说明触发；重复运行可复现；对材料缺失、引用找不到原文、编号不连续、超长截断、格式/编码异常均显式处置。

**生态贡献（10）**
- [ ] `references/` 说明与 `scripts/` 三个脚本可被他人复用；说明与提交版本一致。

## 6. 降级策略（缺数据/缺软件如实标注，禁止编造）

- 材料缺失/无法判断：登记 `uncertainty` + `version_boundary.material_gaps`，`status=partial`，写"材料缺失，无法判断"，不补造数据或引用。
- 引用找不到原文/结论与文献冲突：标"存疑引用"/"疑与文献冲突"，保留作者原意，不擅自改删。
- 缺软件：不谎称渲染成功；无渲染工具时交付可编辑文本，在 `sources`/`uncertainty` 注明"未渲染预览，原因：缺少 X"。
- 超长截断：分段处理并记录段落边界；编号/交叉引用分段后重校。
- 编号不连续/术语冲突：`check.py` 输出 error/warning 先修订，无法自动判定交 `human_confirm`。
- 运行失败：保留已落盘骨架，`status=failed`，`anomalies` 记失败原因与时间；同策略重试 ≤2 次。
- 语向不支持：明确写"未支持语向 X→Y"，只交付源文本核对结果，`status=partial`。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并保留来源证据。

## 7. 双语稿件格式约定（供 check.py 解析）

句段对齐块，段 id 连续递增，SRC/TRG 一一对应：

```text
## §3 方法（Methods）
> SRC[12]|loc:paper.pdf#p4|原句...
> TRG[12]|术语注:…|译文...
```

- SRC/TRG 段 id 一一对应；公式、表格、题注、脚注、参考文献条目保留原位（`$$...$$`、表格块、脚注行）。
- 数字/单位/引用标记保留原值原格式；中英文数字表达差异（万/亿 ↔ million/billion）只做自然表达转换且不改变数值，不得四舍五入或"顺手改成整数"。
- 术语译法以 `glossary.json` 为准；同一术语在摘要、正文、图表题注、附录中保持同一译法。

`glossary.json` 模板：

```json
{"terms":[{"canonical":"机器学习","variants":["machine learning","ML"]}]}
```

## 8. 边界

- 一个 Skill 只解决"学术翻译与双语写作"一类任务，不做通用大而全系统。
- 无真实材料时输出"材料不足"，不用示例填充。
- 交付前在非作者环境跑一遍 `scripts/selftest.py`。
