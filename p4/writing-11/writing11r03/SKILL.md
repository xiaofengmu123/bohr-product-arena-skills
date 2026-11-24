---
name: bilingual-scholarly-writer
description: >-
  Academic translation and bilingual scholarly writing workflow over fixed
  source materials. Extract and lock a terminology glossary, keep numbers,
  units, ranges, years and citations verbatim, emit sentence-aligned bilingual
  deliverables, and write /app/output/result.json with case_id plus the full
  output_contract (deliverables, fact_source_map, version_boundary) and
  status/sources/uncertainty/pending_human/limitations/change_log. Deterministic
  by design: the same input twice produces the same deliverables. Trigger when
  a case asks for translation, bilingual writing, terminology, number/unit or
  citation fidelity, or structured bilingual revision.
version: 1.0.0
---

# 学术翻译与双语写作 Skill（bilingual-scholarly-writer）

## 1. 角色与何时使用

你是一名学术翻译与双语写作 Agent。当案例要求"依据给定科研材料，产出整篇译文、双语对照稿或带修订标记的改稿"，且需要把每个术语、数字、单位、引用、否定与限定条件追溯到原文时，启用本 Skill。

本 Skill 锁定题目四个硬指标：**术语稳**（同一术语在摘要、正文、图题、附录用同一译法）、**数字实**（数字、单位、量级、范围、年份、样本量、统计口径原样落地，不做四舍五入或单位换算）、**对得齐**（原文与译文句段一一对应，公式、表格、题注、脚注、参考文献条目原位保留）、**边界清**（增译、省译、否定范围、因果强度、模糊限制语、引用归属不越过原文语义）。

本版设计取舍是**自检与确定性**：先把事实与术语抽取成机器可核对的结构，再翻译，最后跑 `scripts/selftest.py` 自动校验；全程固定顺序、固定术语表、无随机抽样，保证同一输入两次运行产物一致。

## 2. 输入契约

运行时输入在 `/input/`：

- `/input/case.json`：案例定义，必须真实读取；字段含 `case_id`（或 `id`/`caseId`）、任务说明、材料路径、受众/文体/格式约束，可选的 `output_contract`。
- `/input/task.md`：题面与运行契约，可能重复描述约束。
- 科研材料：`/input/` 下的 PDF/Office/文本/表格等。**PDF 只有原始 PDF，无预提取答案层**，必须用平台 PDF 工具真实解析正文，不得凭文件名臆测内容。

工作目录用 `/app/work/`；最终产物与可编辑工件写入 `/app/output/`。

**脚本路径约定**：脚本位于 Skill 根目录 `scripts/` 下；若当前目录不是 Skill 根目录（运行时通常是 `/app/work` 或 `/skills/weekly-skill`），先把 `scripts/read_case.py` 解析为 Skill 根目录（默认 `/skills/weekly-skill/scripts/read_case.py`）的绝对路径再执行。全部脚本只用 Python 标准库，命令统一为 `python3 scripts/read_case.py ...`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 JSON，原子写入，`sort_keys` 序列化）。字段：

1. `case_id`：来自 `/input/case.json`，原样保留。
2. **题目 output_contract 字段全部原样包含**。若 `case.json` 声明了 `output_contract`，逐字段按声明键名写入；未声明时用默认三字段：
   - `deliverables`（目标产物）：数组，每项 `{path, format, title, description}`；`path` 指向 `/app/output/` 下真实存在的可编辑工件（`.md`/`.tex`/`.docx`/`.pptx`/图）。Office/图类须再附真实渲染的 PDF/PNG 预览路径。
   - `fact_source_map`（事实与来源映射）：数组，每项 `{item, kind, value, source, note, locator, quote}`；`kind ∈ number|term|symbol|citation|figure|claim`；`locator` 给 `文件:页码` 或 `文件:行号`，`quote` 给逐字原文片段。
   - `version_boundary`（版本与边界）：对象，含 `modifications`（`{what, why, before_after, locator}`）、`human_confirm`、`material_gaps`、`anomalies`、`editable`（布尔）。
3. `status`：`success | partial | failed`（运行时骨架阶段可用 `in_progress`，最终必为前三者之一；未完成必须 `partial`/`failed`）。
4. `sources`：实际读过并使用的材料清单 `{path, type, role}`。
5. `uncertainty`：存疑/未确认项 `{item, reason, resolution}`。
6. `pending_human`：需人工确认项 `{item, reason, suggested_action}`。
7. `limitations`：材料/软件/格式缺口 `{item, cause, impact}`。
8. `change_log`：每次落盘/修改 `{time, action, key, detail}`，由脚本自动追加。
9. `runtime`：`{created_at, input_signature, deterministic_signature}`，供重复运行一致性核对。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应含推理 ≤16384 token）

**硬性规则：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进，不得推倒重写。**

- **P0 读题盘点（0–1 分钟，约 2 次请求）**：读 `/input/case.json`、`/input/task.md`、材料清单。执行 `python3 scripts/read_case.py --brief /app/work/case_brief.json`。输出 `case_brief.json`（case_id、output_contract 键清单、材料清单、编码异常、input_signature）。
- **P1 落盘骨架（1–3 分钟，约 1–2 次请求）**：执行 `python3 scripts/write_result.py init`。输出含 case_id + 全部 output_contract 字段的空骨架，`status=in_progress`，change_log 首条 `init`。**此为最低保障产物，务必完成。**
- **P2 抽取+翻译（3–12 分钟，约 35 次请求）**：真实解析源材料；按第 5 节规则先抽术语/数字/引用 → 写 `glossary.json` 与事实草稿，再按"句段"逐节译出到 `/app/output/`；每条术语/数字/引用/论断随手记入 `fact_source_map`。每完成一节或约 2 分钟，用 `python3 scripts/write_result.py add-list deliverables '{"path":"...",...}'`、`add-list fact_source_map '{...}'` 增量落盘。
- **P3 自检与修订（12–14 分钟，约 6 次请求）**：执行 `python3 scripts/selftest.py --out /app/output/result.json --output-dir /app/output --glossary /app/work/glossary.json --report /app/work/selftest_report.json`。按报告逐条修订，差异记入 `version_boundary.modifications`，无法确认者记入 `uncertainty`/`pending_human`。
- **P4 收尾（14–15 分钟，约 3 次请求）**：执行 `python3 scripts/write_result.py finalize --status <success|partial>`；再跑一次 selftest 确认退出码为 0（或仅有 warning）；核对第 6 节自检清单。余量约 15 次请求作为失败重试与额外修复缓冲。

## 5. 翻译规则（决定产物正确性）

- **术语稳**：先建 `glossary.json`（`{terms:[{canonical, variants:[...]}]}`）；`canonical` 为全文锁定译法，含摘要、正文、图题、表题、附录。同一英文词在不同学科译法不同时，为每个学科义项建独立条目并注 `note`。输出只允许 canonical，出现 variant 即记入自检 warning。
- **数字实**：数字、单位、量级、范围、年份、样本量、百分数、有效数字一律原样；`万/亿` 与 `million/billion` 按原文数值换算后显式记录换算式（如 "12 million = 1200 万"），不得"顺手取整"。数字改动只允许在 `version_boundary.modifications` 登记。
- **对得齐**：按句段级对照输出（建议 `|原文句段|译文句段|locator|` 表格），段落顺序、图表编号、公式、题注、脚注、参考文献条目原位保留，不重排。
- **边界清**：保留增译/省译标记、否定范围、因果强度、模糊限制语（如 "suggest/indicate/may/could" 不得升格为 "证实"）、引用与归属（`et al.`、上标、方括号、作者—年份）按目标格式原样映射。
- **确定性**：遍历文件按字典序；同一条判断只落到 glossary.json 或 fact_source_map，不依赖模型瞬时措辞；禁止任何随机抽样、随机措辞选择、随机编号。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 实际效果：每个 deliverable 是整篇译文/双语对照稿，覆盖章节、图表、公式、脚注与参考文献，可直接进入作者修改流程。
- [ ] 实际效果：每条术语/数字/引用/论断在 `fact_source_map` 有 `locator`+`quote`，不新增材料外事实。
- [ ] 使用增益：已运行 `selftest.py` 并保留报告；术语漂移、数字缺失、悬空引用已修复或登记。
- [ ] 使用增益：`version_boundary.modifications` 记录"改了什么、为什么、前后差异"，局部改动未扩大波及范围。
- [ ] 创新增益：术语表+句段对齐+确定性自检形成基础 Agent 不易稳定复现的双语写作能力。
- [ ] 可靠体验：Skill 按说明触发；重复运行产物一致；材料缺失、引用找不到原文、超长截断、格式不支持均显式处置（见第 7 节）。
- [ ] 生态贡献：`references/checklist.md`、`references/bilingual_rules.md` 与三个脚本可被他人复用，说明与提交版本一致。

## 7. 降级策略（缺数据/缺软件必须如实标注，禁止编造）

- **材料缺失/无法判断**：登记 `uncertainty`、`limitations` 与 `version_boundary.material_gaps`，`status=partial`，写"材料缺失，无法判断"，不补造数据、引用或译文。
- **引用找不到原文/语义冲突**：标注"存疑引用/语义疑冲突"，保留原文语义，不擅自改写或删改。
- **缺软件**：无 PDF/Office/渲染工具时不谎称渲染成功；交付可编辑文本并在 `sources`/`limitations` 注明"未渲染预览，原因：缺少 X"。
- **超长截断**：分段翻译并记录段落边界；编号/交叉引用分段后重跑 selftest 校验。
- **运行失败**：保留已落盘骨架，`status=failed`，在 `version_boundary.anomalies` 记录原因与时间；任一策略失败后重试不超过两次。

**红线**：不编造评测分数、运行结果、文献、数据或渲染产物；不确定就写"未确认"并保留来源证据。
