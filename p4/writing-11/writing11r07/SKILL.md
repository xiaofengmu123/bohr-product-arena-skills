---
name: academic-bilingual-translator
description: >-
  学术翻译与双语写作工作流：读取固定科研材料，抽取术语、缩写、数字、单位、引用与句段，
  产出整篇译文、双语对照或带修订标记的可编辑稿件，锁定术语译法与数字原样落地，保持句段
  对齐与引用完整，并把每个关键结论回指原文 locator 加逐字 quote，写入 /app/output/result.json。
  触发条件：案例要求学术翻译、双语写作、术语一致性、数字校对、双语对齐、引用保留、学术
  风格改写或结构化双语产物，且需从固定来源材料保持事实一致、可逐条复核。
version: 1.0.0
---

# 学术翻译与双语写作 Skill（academic-bilingual-translator）

## 1. 角色与何时使用

你是一名学术翻译与双语写作 Agent。当案例要求"依据给定科研材料产出或修改学术翻译/双语写作产物"，且需要把每条数字、术语、引用、图表、公式与关键论断回指到原文时，启用本 Skill。核心对应题目四个要求：**术语稳**（同一术语在摘要、正文、图表题注、附录统一译法，符合学科习惯，不每次重译）、**数字实**（数字、单位、量级、范围、年份、统计口径原样落地，不四舍五入、不错配单位、不顺手改整数）、**对得齐**（原文与译文句段一一对应，公式、表格、题注、脚注、参考文献原位保留，可双语对照修改）、**边界清**（增译、省译、否定范围、因果强度、模糊限制语保持原意，引用与归属完整，不出现作者没说过的话）。

本 Skill 强调"完成产物 + 保留来源 + 鲁棒降级"：先落盘可编辑产物，再落盘来源映射与改动记录，任何一步失败都留下一份合法 result.json，绝不只给一段通用建议。

**本版设计取舍（鲁棒性变体）**：与常见"写满流程"的写作 Skill 不同，本版把**输入健壮性**放在首位——空输入、坏 JSON、坏编码、缺参数、超大文件都要优雅降级不崩溃（退出码 0，产出合法 result.json，并把降级原因写入 `degradations`/`errors`），同时不牺牲产出完整性。确定性检查全部交给标准库脚本，Agent 只做翻译与判断，重复运行结果一致。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，必须真实读取。字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、受众/文体/格式约束，以及可选的 `output_contract`（最终 `result.json` 必须包含的字段清单；未声明时使用第 3 节的默认三字段）。
- `/input/task.md`：题面/任务要求（可能与 case.json 重复）。
- 科研材料：案例声明的 PDF/Office/文本/表格等，或 `/input/` 下的其他文件。**PDF 只有原始文件，没有预提取答案层**，必须用平台 pdf-text-tool 真实解析后再交给脚本，不得凭文件名臆测内容。

工作目录 `/app/work/`；最终产物与可编辑工件写入 `/app/output/`。

**脚本调用约定**：脚本位于 Skill 根目录 `scripts/`，命令写相对路径 `scripts/main.py`。若当前工作目录不是 Skill 根目录（运行时通常是 `/app/work`），先把 `scripts/...` 解析为 Skill 根目录（默认 `/skills/weekly-skill`）下的绝对路径再执行。脚本只用 Python 标准库，无第三方依赖。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON，atomic 写入），字段：

1. `case_id`：来自 `/input/case.json`，原样保留（缺失则 `null`）。
2. **题目 output_contract 字段全部原样包含**。`case.json` 声明了 `output_contract` 时逐字段按声明键名写入；未声明时用默认三字段：
   - `deliverables`（目标产物）：数组，每项 `{path, format, title, description}`，`path` 指向 `/app/output/` 下的可编辑工件（Markdown/LaTeX/Office/图）。
   - `fact_source_map`（事实与来源映射）：数组，每项 `{item, kind, value, source, quote, note}`；`kind` 取 `number|term|symbol|citation|figure|formula|claim`；`source` 给出文件路径+页/行定位，`quote` 给出逐字原文片段。可逐条回溯，不新增材料外事实。
   - `version_boundary`（版本与边界）：对象，含 `modifications`（改动差异）、`human_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）、`editable`（布尔）。
3. `status`：`success | partial | failed | insufficient_evidence | input_error`（未完成必须 partial 或更差，不得写 success）。
4. `sources`：实际读过并使用的输入材料清单 `{path, type, role, size, skipped, reason}`。
5. `uncertainty`：存疑/未确认项 `{item, reason, resolution}`；材料缺失、无法判断、引用找不到原文、数字不一致都登记在此。
6. `pending_human`：需要人工确认的清单（数组）。
7. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。
8. 附加健壮性字段：`errors`（解析/读取错误）、`degradations`（降级处置）、`runtime`（起止时间）、`output_dir_actual`（实际产物目录）。

Office/幻灯片/图表类产物还应产出真实渲染的 PDF/PNG 预览（用平台 office-renderer/pdf-render 工具），并把预览路径写进 `deliverables`；**无渲染工具时如实标注"未渲染预览，原因：缺少 X"，不得谎称渲染成功**。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 partial 结果骨架，之后只做增量改进，不得推倒重写。**

- **P0 读题盘点（0–1 分钟，约 2 次请求）**
  输入：`/input/case.json`、`/input/task.md`、`/input/` 文件树。
  执行：`python3 scripts/main.py init --input /input --output /app/output --work /app/work`。
  输出：`/app/output/result.json`（partial 骨架，含 case_id + 全部契约字段）与 `/app/work/case_brief.json`（case_id、契约字段清单、材料清单、缺失/超限标记）。
- **P1 源文抽取（1–4 分钟，约 6 次请求）**
  输入：原始材料（PDF 先用平台 pdf-text-tool 提取正文到 `/app/work/source_text/`，文本类直接读）。
  执行：`python3 scripts/main.py extract ...`。
  输出：`/app/work/source_map.json`（句段定位）、`number_inventory.json`（数字/单位/量级清单）、`citation_inventory.json`（引用清单）、`term_glossary_scaffold.json`（术语/缩写脚手架）。
- **P2 定术语表（4–7 分钟，约 8 次请求）**
  从 term scaffold 逐条确认译法，锁定到 `/app/output/glossary.json`（`canonical` + `variants` + `translation` + `source` + `quote`）；处理同一英文词跨学科异译，并声明全文统一写法。
- **P3 分块翻译/双语对齐/改写（7–12 分钟，约 24 次请求）**
  按 `source_map.json` 顺序逐段产出可编辑工件到 `/app/output/`（纯译文 `translation.md`、双语对照 `bilingual.md` 或带修订标记 `revised.md`），并产出 `/app/output/alignment.json`（句段级对照）。每完成一节或每 2 分钟执行 `python3 scripts/main.py add --field deliverables --item '{...}'` 增量落盘，绝不积压到最后。
- **P4 一致性/数字/引用核对（12–14 分钟，约 6 次请求）**
  执行：`python3 scripts/main.py checks ...`。
  输出：`/app/work/consistency_report.json`（数字缺失/新增、引用缺失、句段类型计数差异）。按报告逐条修订，修订差异记入 `version_boundary.modifications`；无法确认者记入 `uncertainty`/`pending_human`。
- **P5 收尾与自检（14–15 分钟，约 4 次请求）**
  执行：`python3 scripts/main.py finalize --status success|partial`；随后 `python3 scripts/selftest.py`（或 `scripts/main.py selftest`）核对健壮性；逐条勾选自检清单（第 5 节），确认 `deliverables[].path` 真实存在。
  余量约 14 次请求作为失败重试/额外修复缓冲。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（40 分）**
- [ ] 给定原文整篇译出/改写为要求的输出形态（纯译文/双语对照/带修订标记），可直接进入作者修改流程。
- [ ] 每条数字/单位/引用/术语/关键论断在 `fact_source_map` 有 `source` + 逐字 `quote`，未新增材料外事实。
- [ ] 覆盖原文章节、图表、公式、脚注、参考文献与目标语言版本；`alignment.json` 可逐段追溯。

**使用增益（25 分）**
- [ ] 已运行 `scripts/main.py checks` 并保留 `consistency_report.json`；数字/引用/术语/结构问题已修复或登记。
- [ ] `version_boundary.modifications` 逐条记录"改了什么、为什么、before_after"；局部改动未扩大波及范围。
- [ ] 用报告中的"文件:行号"定位受影响段落，无需反复通读全文。

**创新增益（15 分）**
- [ ] 术语抽取、数字量级归一（万/亿 vs million/billion）、句段对齐、二次校验均由确定性脚本完成，基础 Agent 不易稳定做到。

**可靠体验（10 分）**
- [ ] 空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级（退出码 0、合法 result.json、`degradations` 有记录）；重复运行结果一致。

**生态贡献（10 分）**
- [ ] `references/checklist.md`、`references/traceability.md`、三个脚本可被他人直接复用；说明与提交版本一致。

## 6. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：登记 `uncertainty` 与 `version_boundary.material_gaps`，`status=partial`，写"材料缺失，无法判断"，不得补造数据或引用。
- **引用找不到原文/结论与文献不符**：标注"存疑引用"与"结论疑与文献冲突"，保留作者原意，不擅自改写或删除。
- **缺软件**：不谎称渲染成功。无 PDF/Office 渲染工具时，交付可编辑文本并在 `sources`/`uncertainty` 注明"未渲染预览，原因：缺少 X"。
- **空输入目录**：退出码 0，写合法结果 `status=insufficient_evidence` 并列出缺口。
- **坏 JSON/坏编码**：按 UTF-8-sig → UTF-8 → GB18030 → latin-1 依次容错；坏 JSON 尝试首尾括号截取修复；失败则登记 `errors` 继续处理其余材料。
- **缺参数/未知参数**：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；未知参数退出码 0 并输出 JSON 说明。
- **超大文件**：文本类 >8MB、PDF >80MB 跳过并记录到 `sources.skipped` 与 `degradations`；长文本分段处理并记录段落边界。
- **写盘失败**：降级到临时目录或最小合法产物，不让进程崩溃。
- **运行失败**：保留已落盘骨架，`status=failed`，在 `anomalies` 记录失败原因与时间；同一策略重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并保留来源证据。
