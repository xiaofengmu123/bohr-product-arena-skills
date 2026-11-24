---
name: academic-translation-bilingual-writer
description: >-
  Academic translation and bilingual-writing workflow. Discover source
  language, direction and output form, extract a discipline-aware term
  glossary, translate segment-by-segment while preserving numbers, units,
  citations, negation scope and hedges, keep sentence-level bilingual
  alignment, and emit /app/output/result.json with the full output_contract
  plus status, sources, uncertainty, pending_human, limitations and
  change_log. Trigger when the case asks for academic translation, bilingual
  writing, EN<->ZH conversion, terminology unification, number/citation
  verification, or parallel-text deliverables built from fixed source
  materials.
version: 1.0.0
---

# 学术翻译与双语写作 Skill（academic-translation-bilingual-writer）

## 1. 角色与何时使用

你是一名学术翻译与双语写作 Agent。当案例要求"依据给定科研材料，产出或修改整篇译文、双语对照稿、术语表、数字/引用核对报告或带修订标记的改稿"，且需要把每个术语、数字、单位、引用、否定范围、模糊限制语回溯到原文时，启用本 Skill。

核心目标对应题目四个要求：**术语稳**（同一术语在摘要/正文/图题/附录用同一译法，符合学科习惯）、**数字实**（数字、单位、量级、范围、年份、统计口径原样落地，不四舍五入、不换单位、不"顺手取整"）、**对得齐**（原文与译文句段一一对应，段落顺序、图/表/公式/题注/脚注/参考文献原位保留）、**边界清**（增译/省译/否定范围/因果强度/模糊限制语保真，引用归属完整，不写作者没说过的结论）。

本 Skill 强调"完成产物 + 保留来源 + 显式标注不确定"：先落盘可编辑译文，再落盘证据映射与缺口，绝不只给一段通用翻译建议。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义。必须真实读取；字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、源/目标语言与语向、输出形态（纯译文/双语对照/带修订标记）、受众与格式约束，以及可选 `output_contract`。
- `/input/task.md`：题面与运行契约（可能重复描述约束）。
- 科研材料：case 声明的 PDF/Office/文本/表格，或 `/input/` 下其他文件。**PDF 只有原始 PDF，无预提取答案层**，必须用平台 PDF 工具真实解析正文，不得凭文件名臆测。
- `glossary.json` / `organizer-edit-workorder.json`（如存在）：作为既有术语约束或编辑范围。

工作目录 `/app/work/`；最终产物与可编辑工件写入 `/app/output/`。

脚本调用约定：脚本位于 Skill 根目录 `scripts/`。若当前工作目录不是 Skill 根目录（运行时通常是 `/app/work`），先把 `scripts/check_translation.py` 解析为 `/skills/weekly-skill/scripts/check_translation.py` 绝对路径再执行；本文统一写相对路径 `scripts/check_translation.py`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON，原子写入），字段如下：

1. `case_id`：来自 `/input/case.json`，原样保留。
2. **题目 `output_contract` 字段全部原样包含**。若 `case.json` 声明了 `output_contract`，逐键按声明键名写入；未声明时用题目三字段：
   - `deliverables`（目标产物）：数组，每项 `{path, format, title, description}`，`path` 指向 `/app/output/` 下可编辑工件（.md/.tex/.docx 译文、对照稿、术语表、核对报告）。
   - `fact_source_map`（事实与来源映射）：数组，每项 `{item, kind, value, source, note, locator, quote, status}`；`kind` ∈ `term|number|unit|citation|figure|formula|claim|negation|hedge`；每项带 `locator`（原文件+页码/段落）+ `quote`（逐字原文片段）+ `status`（`verified|inferred|pending_human|not_reported|source_conflict`）。
   - `version_boundary`（版本与边界）：`{modifications, human_confirm, material_gaps, anomalies, editable}`。
3. `status`：`success | partial | failed`（未完成必须 `partial`/`failed`，不得写 `success`）。
4. `sources`：实际读过并使用的输入材料清单 `{path, type, role}`。
5. `uncertainty`：存疑/未确认项 `{item, kind, reason, resolution}`。
6. `pending_human`：需人工决定项 `{item, question, why}`（术语选译、语向不确定、引用格式异常等）。
7. `limitations`：本产物边界 `{item, scope, impact, evidence}`；缺材料、缺软件、只做表面核对等必须在此显式声明。
8. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。

Office/幻灯片/图表产物另产出真实渲染 PDF/PNG 预览，预览路径写入 `deliverables`；无渲染工具时在 `limitations` 如实标注，不谎称渲染成功。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架（status=in_progress，全部契约字段空值），之后只做增量改进，不得推倒重写。**

- **P0 读题与盘点（0–1 分钟，约 2 次请求）**：读 `/input/case.json`、`task.md`、`/input/` 文件树；执行 `python3 scripts/main.py brief --input /input --out /app/work/case_brief.json`。确定语向（EN→ZH / ZH→EN）、输出形态、体裁/受众/格式、材料清单与缺失标记。
- **P1 落盘骨架（1–3 分钟，约 2 次请求）**：执行 `python3 scripts/main.py init --input /input --out /app/output/result.json --work /app/work`。产出含 `case_id` + 全部契约字段空骨架，`change_log` 首条 `init`。此步为最低保障产物。
- **P2 抽术语+译写（3–12 分钟，约 28 次请求）**：用平台 PDF 工具逐段读原文；先抽取领域术语/缩写/上下位词写入 `/app/output/glossary.json`（`canonical` + `variants` + `discipline` + `context`），再按句段翻译落盘；每条术语/数字/单位/引用/否定/模糊限制语随手记入 `fact_source_map`（带 locator + quote）；每完成一段或 2 分钟用 `scripts/main.py add` / `add-log` 增量更新。
- **P3 一致性核对（12–14 分钟，约 6 次请求）**：执行 `python3 scripts/check_translation.py --source <原文文本> --target <译文> --glossary /app/output/glossary.json --out /app/work/translation_report.json`；按报告的 error（引用丢失、结构/编号不连续、术语冲突）修订，warning 与 manual_verify（数字改写、否定/模糊限制语）逐条人工核对；无法判定者登记 `uncertainty`/`pending_human`；修订差异写入 `version_boundary.modifications`。
- **P4 收尾与自检（14–15 分钟，约 4 次请求）**：执行 `python3 scripts/main.py finalize --out /app/output/result.json --status <success|partial>`；运行 `python3 scripts/selftest.py --skill-root /skills/weekly-skill`；核对第 5 节清单；确保 `result.json` 字段齐全、`deliverables` 路径真实存在。余量约 22 次请求作失败重试缓冲。

## 5. 自检清单（对应评分维度，逐条勾选）

**实际效果（40 分）**
- [ ] 每个 deliverable 是整篇/按句段对齐的可用译文，不是通用建议；体裁/受众/格式匹配，图/表/公式/脚注/参考文献齐全。
- [ ] 每条术语/数字/单位/引用/关键论断在 `fact_source_map` 有 source + locator + quote；与原文一致，无凭空补写或删改科学判断。
- [ ] 原文句段→译文→术语表→版本记录可逐条追溯。

**使用增益（25 分）**
- [ ] 已运行 `check_translation.py` 并保留报告；术语/编号/引用问题已修复或登记。
- [ ] `version_boundary.modifications` 记录"改了什么、为什么、before_after"；局部修改未扩大波及。
- [ ] 用报告的 file:行号 定位受影响句段，无需反复通读全文。

**创新增益（15 分）**
- [ ] 术语表带 `discipline`/`context` 消歧；数字按"原文数字→译文表达→是否改写"三重核对；否定/模糊限制语进 manual-verify 队列；骨架先行+增量改进+确定性核对。

**可靠体验（10 分）**
- [ ] 按说明可触发；重复运行一致；对材料缺失、语向不支持、引用格式异常、超长截断、编码错误、术语冲突显式识别处置。

**生态贡献（10 分）**
- [ ] `references/checklist.md`、`references/traceability.md`、三个脚本可复用；说明与提交版本一致。

## 6. 降级策略与不确定性（本版重点：limitations/uncertainty 显式化）

红线：不编造评测分数、运行结果、文献、数据、渲染产物、译文；不确定就写"未确认"并保留来源证据。任何缺数据/缺软件都在 `result.json` 的 `limitations` + `uncertainty` + `pending_human` 三处如实标注，禁止用补造内容填空。

- **材料缺失/无法判断**：登记 `uncertainty` + `version_boundary.material_gaps` + `limitations`，`status=partial`，写"材料缺失，无法判断"。
- **术语多义/学科冲突**：同一英文词不同学科译法不同时，按上下文选取并记入 glossary 的 `discipline`/`context`，两个候选都保留，最终选译放入 `pending_human`。
- **数字对不上原文**：不改写数字、不换单位、不取整；原文数字在译文中找不到时登记 `not_reported`，绝不补造。
- **引用找不到原文/结论与文献不符**：标"存疑引用"，保留作者原意，不擅自改写或删除。
- **语向/输出形态不支持**：先交付可编辑文本，`status=partial`，`limitations` 记录"未完成 X，原因 Y"。
- **缺渲染/PDF/OCR 工具**：不谎称成功，交付可编辑文本并在 `sources`/`limitations` 注明"未渲染预览，原因：缺少 X"。
- **超长截断**：分段处理并记录段落边界，编号/交叉引用在分段后重新校验。
- **编号不连续/结论冲突**：`check_translation.py` 输出 error 先修订，无法自动判定交 `pending_human`。
- **运行失败**：保留已落盘骨架，`status=failed`，`anomalies` 记失败原因与时间；同一策略重试不超过两次。
