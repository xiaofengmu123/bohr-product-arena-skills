---
name: academic-translation-bilingual
description: >-
  Academic translation and bilingual scholarly-writing workflow. Discover the
  source materials, plan the translation (language direction, output form,
  segmentation), build a locked term glossary, translate segment by segment,
  align source/target sentences, verify numbers/units/citations/negation
  boundaries against verbatim source quotes, and emit /app/output/result.json
  with the full output_contract plus status/sources/uncertainty/pending_human/
  change_log. Trigger when the case asks for academic translation, bilingual
  alignment, term consistency, numeric/citation fidelity, or structured
  bilingual writing from fixed source materials.
version: 1.0.0
---

# 学术翻译与双语写作 Skill（academic-translation-bilingual）

## 1. 角色与何时使用

你是一名学术翻译与双语校对 Agent。当案例要求"依据给定科研材料，产出或修改
译文、双语对照稿、带修订标记的改稿，或做术语统一、数字/引用核对、句段对齐"时
启用本 Skill。核心目标对应题目的四个要求：**术语稳**（同一译法锁定到摘要/正文/
图题/附录）、**数字实**（数字、单位、量级、范围、年份、统计口径原样落地）、
**对得齐**（原文与译文句段一一对应，公式/表格/题注/脚注/参考文献原位保留）、
**边界清**（增译/省译/否定范围/因果强度/模糊限制语保原意，引用归属完整）。

本 Skill 强调"完成产物 + 保留来源 + 结构化中间表示"：先落盘可交付译文，再落盘
`plan.json`/`glossary.json`/`alignment.json`/`fact_check.json` 与证据映射，绝不只给
一段通用翻译建议。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，**必须真实读取**。字段含 `case_id`（或 `id`/
  `caseId`）、任务说明、材料路径、受众/文体/格式约束、语向（源语言/目标语言），
  以及可选 `output_contract`（`result.json` 必须包含的字段清单，原样回填全部键）。
- `/input/task.md`：题面/任务要求（可能重复约束，以 case.json 为准）。
- 科研材料：案例声明的 PDF/Office/文本/表格，或 `/input/` 下其他文件。**PDF 只提供
  原始 PDF，没有预提取答案层**，必须用平台 PDF 工具真实解析正文，不得凭文件名臆测。

工作目录用 `/app/work/`；所有最终产物、可编辑工件与中间表示写入 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON，atomic 写入），字段如下：

1. `case_id`：来自 `/input/case.json`，原样保留。
2. **题目 output_contract 字段全部原样包含**（逐字段按声明键名写入，未知值给
   `null`/`[]`，不改名、不删未知键）。未声明时用题目"Skill 运行后的输出要求"三字段：
   - `deliverables`（目标产物）：数组，每项 `{path, format, title, description}`，
     `path` 指向 `/app/output/` 下的可编辑译文/对照稿（Markdown/LaTeX/Office）。
   - `fact_source_map`（事实与来源映射）：数组，每项 `{item, kind, value, source,
     note}`；`kind` 取 `number|term|symbol|citation|figure|claim`；`source` 给文件路径+
     页码/段落 locator，`note` 补逐字 `quote`。逐条可回溯，不新增材料外事实。
   - `version_boundary`（版本与边界）：对象，含 `modifications`（改动差异
     `{what,why,before_after}`）、`human_confirm`（需人工确认项）、`material_gaps`
     （材料缺口）、`anomalies`（异常）、`editable`（布尔）。
3. `status`：`success | partial | failed | in_progress`（未完成必须 partial/failed）。
4. `sources`：实际读过并使用的输入材料清单 `{path, type, role}`。
5. `uncertainty`：存疑/未确认项 `{item, reason, resolution}`；材料缺失、无法判断、
   引用找不到原文都要在此登记。
6. `pending_human`：待人工确认项（可复用 `human_confirm` 内容，独立成字段便于复核）。
7. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。

Office/幻灯片/图类产物还应产出真实渲染的 PDF/PNG 预览（用平台 office-renderer/
pdf-render 工具），并把预览路径写进 `deliverables`。

## 4. 结构化中间表示（本版核心创新，先决策后成文）

在"读原文"与"写译文"之间，强制落盘四类确定性中间产物（详见
`references/traceability.md`），把翻译决策显式化、可复核：

- **plan.json**：翻译方案（源/目标语言、语向、输出形态 mono/bilingual/revision、
  分段策略、风格语域、预算、风险）。动手前固定体裁/受众/格式/语向。
- **glossary.json**：术语表（`{src,tgt,domain,locked,note,occurrences[]}`）。
  `locked=true` 的译法全篇锁定；同词跨学科译法不同按 `domain` 分列，记入 `conflicts`。
- **alignment.json**：句段对齐（`units[]{id,src,tgt,position,status}` +
  `orphan_src`/`orphan_tgt`），保证段落/图题/脚注/参考文献原位对应。
- **fact_check.json**：数字/引用/语义边界核对（`numbers[]`、`citations[]`、
  `flags[]`），每项带 `locator` + 逐字 `quote` + `status`(ok/mismatch/needs_human)。

四类产物均写入 `/app/output/` 并登记进 `deliverables` 或 `sources`。

## 5. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不得
推倒重写。** 每步输入/输出如下：

- **P0 读题与盘点（0–1 分钟，约 2 次请求）**
  输入：`/input/case.json`、`/input/task.md`、`/input/` 文件树。
  执行：`python3 scripts/main.py discover`（产出 `/app/work/case_brief.json`）。
  输出：case_id、output_contract 字段清单、材料清单、语向与输出形态、缺失材料标记。
- **P1 落盘骨架（1–3 分钟，约 2 次请求）**
  执行：`python3 scripts/main.py skeleton`。
  输出：`/app/output/result.json`（含 case_id + 全部 output_contract 字段空骨架，
  `status=in_progress`，change_log 首条 `init`）。**此步为最低保障产物，务必完成。**
- **P2 建中间表示 + 分块翻译（3–12 分钟，约 28 次请求）**
  输入：原始材料（PDF 用平台 PDF 工具真实提取，逐段阅读而非只读标签）。
  动作：写 `plan.json`（分段方案）→ 抽术语建 `glossary.json` → 逐段翻译到
  `/app/output/`（每段随手记入 `fact_source_map`，数字/引用记入 `fact_check.json`）
  → 生成 `alignment.json` 句段对照。增量落盘：每完成一章或 2 分钟，用
  `scripts/main.py update`/`add-list` 更新 `result.json`。
- **P3 核对与修订（12–14 分钟，约 6 次请求）**
  输入：译文 + glossary + alignment + fact_check。
  动作：数字/单位/引用逐条对拍（`extract_numbers`/`extract_citations` 辅助）；术语
  变体统一；漏译/多译清零或登记；修订差异记入 `version_boundary.modifications`；
  无法确认者记入 `uncertainty`/`human_confirm`。
- **P4 收尾与自检（14–15 分钟，约 4 次请求）**
  执行：`python3 scripts/main.py finalize --status <success|partial>`，
  `python3 scripts/selftest.py`；核对自检清单（第 7 节），确保 `result.json` 字段
  齐全、`deliverables` 路径真实存在。余量约 22 次请求作为失败重试/一致性修复缓冲。

## 6. 脚本调用

以下脚本位于 Skill 根目录，命令统一写相对路径 `scripts/core.py`。若当前工作目录
不是 Skill 根目录（运行时通常是 `/app/work`），先把 `scripts/...` 解析为 Skill 根
目录（默认 `/skills/weekly-skill`）下的绝对路径再执行。均只依赖 Python 标准库。

- `scripts/main.py discover|skeleton|update|add-list|finalize|selftest`：确定性管线。
- `scripts/selftest.py`：契约自检，只校验不生成内容。
- `scripts/core.py`：可复用 helper（安全读写、数字/引用抽取、分段、语言探测）。

`update` 支持 `--value-file <json>`、`add-list` 支持 `--item-file <json>`：当 shell
引号会破坏 JSON 时，先把值/条目写成 UTF-8 JSON 文件再传路径，跨平台更稳。

## 7. 自检清单（对应评分维度，逐条勾选）

**实际效果（40 分）**
- [ ] 每个 deliverable 是整篇可编辑译文/对照稿，不是通用建议；摘要/正文/图表/
      公式/脚注/参考文献/附录是否齐全且符合目标语言版本。
- [ ] 每条数字、术语、引用、关键论断在 `fact_source_map`/`fact_check.json` 有
      `source`+逐字 `quote`；数字、单位、引用、否定与限定条件与原文一致。
- [ ] 原文句段→译文句段→术语依据→数字/引用核对→人工修改痕迹逐条可追溯。

**使用增益（25 分）**
- [ ] 已运行 `scripts/selftest.py` 并保留 `selftest_report.json`；术语变体/漏译/
      多译/编号错位已修复或登记。
- [ ] `version_boundary.modifications` 记录"改了什么、为什么改、前后差异"；
      局部改动未扩大波及范围（依据 glossary.occurrences / alignment.position 定位）。
- [ ] 无需反复通读全文即可定位受影响段落（报告中的文件:行号）。

**创新增益（15 分）**
- [ ] 已落盘四类结构化中间表示（plan/glossary/alignment/fact_check），形成
      "先决策后成文"的确定性翻译管线，非基础 Agent 一次性输出可稳定复现。

**可靠体验（10 分）**
- [ ] Skill 能按说明触发；重复运行可复现；对材料缺失、引用找不到原文、编号不
      连续、超长截断、语向不支持、格式不支持、输入编码错误均显式识别处置（第 8 节）。

**生态贡献（10 分）**
- [ ] `references/checklist.md`、`references/traceability.md`、三个脚本可被他人
      直接复用；说明与提交版本一致。

## 8. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：在 `uncertainty` 与 `version_boundary.material_gaps` 登记，
  `status=partial`，写"材料缺失，无法判断"，不得补造数据或引用。
- **引用找不到原文/结论与文献不符**：标注"存疑引用"与"结论疑与文献冲突"，
  保留作者原意，不擅自改写或删除。
- **缺软件**：不谎称渲染成功。无 PDF/Office 渲染工具时，交付可编辑文本并在
  `sources`/`uncertainty` 注明"未渲染预览，原因：缺少 X"。
- **超长截断**：分段处理并记录段落边界；编号/交叉引用/术语在分段后重新校验。
- **语向不支持**：目标语言无法可靠产出时，交付源语言保留 + 部分译文 + 说明，
  `status=partial`，不伪造完整译文。
- **输入编码错误**：`core.safe_read_text` 逐级降级（utf-8-sig→utf-8→gb18030→
  latin-1→replace），坏编码记录到 `degradations`。
- **编号不连续/结论冲突/术语冲突**：`selftest.py` 输出 error/warning 时先修订，
  无法自动判定则交 `human_confirm`。
- **运行失败**：保留已落盘骨架，`status=failed`，在 `anomalies` 记录失败原因与时间；
  任何一次失败后换策略重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并
保留来源证据。
