---
name: academic-translation-bilingual-writer
description: >-
  Academic translation and bilingual writing workflow from fixed source
  materials. Extract a maintainable glossary, translate in aligned segments,
  verify term stability (术语稳), number/unit fidelity (数字实), source-target
  segment alignment (对得齐), and negation/hedging/citation boundaries (边界清),
  then emit /app/output/result.json with the full output_contract plus
  status/sources/uncertainty/pending_human/change_log. Trigger when the case
  asks for academic translation, bilingual parallel text, terminology
  unification, number/citation verification, or revision with tracked edits.
version: 1.0.0
---

# 学术翻译与双语写作 Skill（academic-translation-bilingual-writer）

## 1. 角色与何时使用

你是一名学术翻译与双语校对 Agent。当案例要求“依据给定科研材料，产出纯译文、双语句段对照或带修订标记的稿件”，且要求术语、数字、单位、引用、否定与限定条件与原文一致时，启用本 Skill。核心目标对应题目的四道质量关：**术语稳**（同一术语在摘要、正文、图表题注、附录保持同一译法）、**数字实**（数字、单位、量级、范围、年份、统计口径原样落地，正确处理万/亿与 million/billion）、**对得齐**（原文与译文句段一一对应，公式、表格、题注、脚注、参考文献原位保留）、**边界清**（增译、省译、否定范围、因果强度、模糊限制语、引用归属不越界）。

本 Skill 强调“完成产物 + 保留来源 + 可复核证据”：先落盘可编辑译文，再落盘逐条可回溯的事实映射（locator + 逐字 quote），绝不只给一段通用翻译建议，也绝不新增材料外事实。

本版设计取舍（相对通用长文档写作 Skill 的差异）：只聚焦学术翻译与双语写作，把题目评分维度逐条映射到流程步骤与可复核证据（完整映射表见 `references/checklist.md`）。四道质量关与 `check_consistency.py` 报告类型一一对应：术语稳 ↔ `term_variant_collision`；数字实 ↔ `number_missing`/`unit_missing`/`percent_missing`；对得齐 ↔ `alignment_short`/图编号跳号/悬空交叉引用；边界清 ↔ `citation_missing`/`fact_quote_not_found`。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，必须真实读取。字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、受众/文体/格式约束、语向（源语言→目标语言）、输出形态（纯译文/双语对照/带修订标记），以及可选的 `output_contract`（最终 `result.json` 必须包含的字段清单）。
- `/input/task.md`：题面，可能与 `case.json` 重复描述约束，以 `case.json` 为准。
- 科研材料：案例声明的 PDF/Office/文本/表格，或 `/input/` 下其他文件。**PDF 只提供原始 PDF，无预提取答案层**，必须用平台 PDF 工具真实解析正文，不得凭文件名臆测内容。
- 如存在 `glossary.json`：优先读取并沿用；否则从原文抽取并新建。

工作目录用 `/app/work/`；所有最终产物与可编辑工件写入 `/app/output/`。

**脚本调用约定**：脚本位于 Skill 根目录 `scripts/` 下，命令统一写为相对路径 `scripts/check_consistency.py`。若当前工作目录不是 Skill 根目录（运行时通常是 `/app/work`），先把 `scripts/...` 解析为 Skill 根目录（默认 `/skills/weekly-skill`）下的绝对路径再执行，例如 `python3 /skills/weekly-skill/scripts/main.py init`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON，atomic 写入），字段如下：

1. `case_id`：来自 `/input/case.json`，原样保留。
2. **题目 output_contract 字段全部原样包含**。`case.json` 声明了 `output_contract` 时逐字段按声明键名写入；未声明时用题目“Skill 运行后的输出要求”三字段：
   - `deliverables`（目标产物）：数组，每项 `{path, format, title, description}`，`path` 指向 `/app/output/` 下的可编辑工件（Markdown/LaTeX/Office/图）。纯译文、双语对照稿、术语表、对照表各为一个 deliverable。
   - `fact_source_map`（事实与来源映射）：数组，每项 `{item, kind, value, target_value, source, locator, quote, note}`；`kind` 取 `term|number|unit|citation|figure|claim|alignment`；`locator` 为“文件:行号或页码段落”，`quote` 为原文逐字片段；逐条可回溯，不新增材料外事实。
   - `version_boundary`（版本与边界）：对象，含 `modifications`（改动差异）、`human_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）、`editable`（布尔）。
3. `status`：`success | partial | failed`（未完成必须 partial/failed）。
4. `sources`：实际读过并使用的输入材料清单 `{path, type, role}`。
5. `uncertainty`：存疑/未确认项 `{item, reason, resolution}`；材料缺失、无法判断、引用找不到原文、术语表冲突都要在此登记。
6. `pending_human`：需人工决策的事项 `{item, options, recommended}`，供作者直接复核。
7. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。

Office/幻灯片/图表类产物还应产出真实渲染的 PDF/PNG 预览（用平台 office-renderer/pdf-render 工具），并把预览路径写进 `deliverables`，便于复核。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不得推倒重写。** 每步输入/输出如下。

- **P0 读题与盘点（0–1 分钟，约 2 次请求）**
  输入：`/input/case.json`、`/input/task.md`、`/input/` 文件树。
  执行：读 `case.json` 取 `case_id`、语向、输出形态、`output_contract` 字段清单；列材料清单并标记缺失。
  输出：`/app/work/case_brief.json`（case_id、语向、输出形态、output_contract 键、材料清单、缺失标记）。
- **P1 落盘骨架（1–3 分钟，约 2 次请求）**
  执行：`python3 /skills/weekly-skill/scripts/main.py init`。
  输出：`/app/output/result.json`（含 case_id + 全部 output_contract 字段的空骨架，`status=in_progress`，change_log 首条 `init`）。**此步为最低保障产物，务必完成。**
- **P2 读材料+抽术语+分块翻译（3–12 分钟，约 28 次请求）**
  输入：原始材料（PDF 用平台 PDF 工具真实提取，逐段阅读而非只读标签）。
  动作：抽取领域术语、缩写、上下位词与数字/单位/引用到 `fact_source_map`（每项含 locator + quote）；按句段分块翻译，先产术语表与对照稿到 `/app/output/`；每完成一章或 2 分钟，用 `main.py add-list --field deliverables --item '{"path":"...", ...}'` 增量更新 `result.json`。
- **P3 一致性检查与修订（12–14 分钟，约 6 次请求）**
  输入：P2 产出的译文文件 + `glossary.json`。
  执行：`python3 /skills/weekly-skill/scripts/check_consistency.py --source /input --target /app/output --glossary /app/output/glossary.json --out /app/work/consistency_report.json`；按报告逐条修订（术语统一、数字/单位/引用补齐、对齐修正），修订差异记入 `version_boundary.modifications`；无法确认者记入 `uncertainty` 或 `pending_human`。
- **P4 收尾与自检（14–15 分钟，约 4 次请求）**
  执行：`python3 /skills/weekly-skill/scripts/main.py finalize --status <success|partial>`；运行 `python3 /skills/weekly-skill/scripts/selftest.py` 核对自检清单（第 5 节）；确保 `result.json` 字段齐全、`deliverables` 路径真实存在。
  余量：约 22 次请求作为失败重试/额外一致性修复缓冲。

## 5. 自检清单（对应评分维度，逐条勾选；scripts/selftest.py 自动复核前 4 类）

**实际效果（40 分）**
- [ ] 每个 deliverable 是整篇可用译文/对照稿，不是通用建议；按要求的输出形态（纯译文/双语对照/带修订标记）交付，可直接进入作者修改流程。
- [ ] 每条数字/术语/引用/否定与限定在 `fact_source_map` 有 `locator` 与逐字 `quote`；值与原文一致，无凭空补写或删改科学判断。
- [ ] 覆盖原文章节、图表、公式、脚注与参考文献及任务要求的目标语言版本；`check_consistency.py` 报告无 error 级缺口。

**使用增益（25 分）**
- [ ] 已运行 `check_consistency.py` 并保留报告；术语/编号/数字/引用问题已修复或登记。
- [ ] `version_boundary.modifications` 记录“改了什么、为什么改”；局部改动未扩大波及范围。
- [ ] 依据报告“文件:行号”即可定位受影响段落，无需反复通读全文。

**创新增益（15 分）**
- [ ] “骨架先行+增量改进”、术语表锁定、句段对齐、数字归一化（万/亿↔million/billion）与二次校验构成基础 Agent 不易稳定做到的长文档双语能力。

**可靠体验（10 分）**
- [ ] Skill 能按说明触发；重复运行可复现（脚本确定性、无随机态）。
- [ ] 对材料缺失、引用找不到原文、术语表冲突、编号不连续、超长截断、语向不支持、格式不支持均显式识别处置（见第 6 节）。

**生态贡献（10 分）**
- [ ] `references/checklist.md`、`references/glossary-template.md`、三个脚本可被他人直接复用；说明与提交版本一致。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：在 `uncertainty` 与 `version_boundary.material_gaps` 登记，`status=partial`，写“材料缺失，无法判断”，不得补造数据或引用。
- **术语表冲突（同词多译）**：`check_consistency.py` 报 warning 时先统一，无法自动判定则写入 `pending_human`（附 options 与 recommended）。
- **引用找不到原文/结论与文献不符**：标注“存疑引用”与“结论疑与文献冲突”，保留作者原意，不擅自改写或删除。
- **缺软件/语向不支持**：不谎称渲染或翻译成功；交付可编辑文本并在 `sources`/`uncertainty` 注明“未渲染预览/未做目标语向，原因：缺少 X”，`status=partial`。
- **超长截断**：分段处理并记录段落边界；编号/交叉引用在分段后重新校验。
- **数字不连续/单位错配/结论冲突**：`check_consistency.py` 输出 error/warning 时先修订，无法自动判定则交 `pending_human`。
- **运行失败**：保留已落盘骨架，`status=failed`，在 `anomalies` 记录失败原因与时间；任何一次失败后换策略重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据、译文或渲染产物；不确定就写“未确认”并保留来源证据。

## 7. 最小调用示例（示意输入/输出，标明为示例，不冒充正式案例结果）

示意输入：`/input/case.json` 含 `case_id: demo-case`、`source_lang: en`、`target_lang: zh`、`output_form: bilingual`；`/input/paper.pdf` 为一段方法描述（已用平台 PDF 工具提取为 `/app/work/source_text.txt`）。

```bash
python3 /skills/weekly-skill/scripts/main.py init
python3 /skills/weekly-skill/scripts/main.py add-list --field deliverables \
  --item '{"path":"/app/output/translation.md","format":"markdown","title":"双语对照稿","description":"句段级对照"}'
python3 /skills/weekly-skill/scripts/check_consistency.py \
  --source /app/work/source_text.txt --target /app/output \
  --glossary /app/output/glossary.json --result /app/output/result.json \
  --out /app/work/consistency_report.json
python3 /skills/weekly-skill/scripts/main.py finalize --status success
python3 /skills/weekly-skill/scripts/selftest.py
```

示意输出 `result.json` 骨架（示例，字段值省略）：

```json
{
  "case_id": "demo-case",
  "status": "success",
  "deliverables": ["..."],
  "fact_source_map": ["..."],
  "version_boundary": {"modifications": ["..."], "human_confirm": [], "material_gaps": [], "anomalies": [], "editable": true},
  "sources": ["..."],
  "uncertainty": [],
  "pending_human": [],
  "change_log": ["..."]
}
```

