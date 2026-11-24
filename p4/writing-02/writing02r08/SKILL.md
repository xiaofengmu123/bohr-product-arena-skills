---
name: academic-polish-02
description: 学术语言润色与保真复核（评分维度对齐版）。对论文/摘要/基金稿/审稿回复/学位论文等科研文本做最小编辑，逐句产出"原文—改文—修改类型—理由"，并用确定性脚本校验限定强度、数字单位、引用与图表指向、术语一致性是否被改坏。当需要润色/改写学术文本且必须保留事实与来源时使用；纯内容问答、生成新实验数据或写全新稿件时不触发。
version: 2.0.0
---

# 学术语言润色与保真复核 Skill（评分维度对齐版）

## 1. 角色与何时使用

本 Skill 让 Agent 在**改写学术文本的同时完成保真复核**，交付"润色后的完整文本＋逐句修改说明"，而非一段写作建议或整段重写。

- **使用**：输入给出事实底稿/稿件/摘要，要求润色、改写、校对、术语统一、中英互译或按体裁（期刊论文、学位论文、基金申请书、审稿回复、英文摘要）调整语域时。
- **不使用**：纯内容问答、生成新实验数据、解释概念、写全新稿件（无底稿）。此时不触发，避免擅自改写。
- **唯一硬约束**：润色只改"表达"，不改"事实与强度"。限定词强度、数据、单位、引用编号、图表指向、因果与相关、否定范围、条件句、量词必须原样保留；术语译法须符合领域惯例。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，不假设固定文件名。扫描并识别：

- `/input/case.json`：任务与材料索引；可能含 `output_contract`、`id`/`case_id`、内联 `text`/`draft`/`原文` 字段。
- `/input/task.md`：任务说明、受众、文体、格式约束、评分要求。
- 材料：`/input/materials/` 或根目录下的 `*.md`、`*.txt`、`*.tex`、`*.pdf`、`*.docx` 等。PDF 用 pdftotext/OCR 解析**真实文件**，不按文件名补造内容。

文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记录。

## 3. 输出契约（写到哪里）

最终产物写入 `/app/output/`，工作中间态写入 `/app/work/`。必须产出：

- `/app/output/result.json`（必需）：`case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）+ `status` / `sources` / `uncertainty` / `change_log`；另附 `pending_human` / `errors` / `degradations` / `runtime` / `gain_metrics` / `check_counts`。
- 默认 output_contract 字段：`deliverable`（可编辑主产物路径）、`fact_source_map`（每项含 `fact`/`locator`/`quote`）、`version_boundary`（差异、人工确认项、缺口）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，诚实标注；`sources` 带路径/类型/字节/摘要指纹。
- `/app/output/manuscript.md`（可编辑正稿：润色全文 + 逐句修改表）、`/app/output/glossary.md`（若有术语表）。Office/slide/diagram 产物必须另附真实渲染的 PDF/PNG 预览；无法渲染时如实说明，不伪造 PDF。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> 铁律：**前 3 分钟内必须落盘一版 result.json 骨架**，之后只做增量改进，任何中途失败都不留空产物。

| 阶段 | 时间 | 动作 | 输入→输出 | 模型请求 |
|---|---|---|---|---|
| 0 脚手架 | 0–15s | 运行 `python3 -B scripts/main.py scaffold` | `/input` → `/app/output/result.json`(partial) + `/app/work/edits.json`(逐句模板) + `/app/work/evidence_index.json` + `/app/work/glossary.json` | 0 |
| 1 读材料 | 15–150s | 读 task.md、case.json、材料；建证据索引与术语表；定 `target_lang`/`genre` | 材料 → `/app/work/glossary.json`、事实池（数字/单位/引用/限定词逐条带 locator+quote） | 3–6 |
| 2 逐句编辑 | 150–480s | 按句填 `edits.json` 的 `units[]`：`original/revised/edit_type/reason/evidence`，关键句填 `hedge_note`；只改表达不改事实 | 底稿 → 完整逐句编辑记录 | 20–30 |
| 3 双通道审校 | 480–660s | 先编辑、后独立复核（对照原文回读/回译）；运行 `python3 -B scripts/main.py verify` | `edits.json` → `check_report.json` | 10–15 |
| 4 修复 | 660–780s | 清零所有 error（数字/单位/引用/覆盖/溯源缺失）；对 hedge 警告逐一"回退或写明 `hedge_note`" | `check_report` → 修订 `edits.json` | 5–10 |
| 5 定稿 | 780–840s | 运行 `python3 -B scripts/main.py finalize` | → `manuscript.md` + `glossary.md` + 完整 `result.json` | 0 |
| 6 自检 | 840–900s | 运行 `python3 -B scripts/main.py selfcheck`；可选 `python3 -B scripts/selftest.py` | 校验 result.json、产物与评分维度对齐 | 0 |

- 脚本从 Skill 根目录调用（相对路径 `scripts/main.py`），缺省 `--input /input --output /app/output --work /app/work`；也可显式传参。
- `scaffold` 先用坏 JSON/空目录/坏编码的降级逻辑写好合法 partial 骨架，保证第 3 分钟前有可交付物。
- 同一输入重复运行结果一致：校验逻辑确定性、无随机数；`runtime` 仅作测量元数据，不编造评测分数。

## 5. 评分维度对齐（本版核心取舍）

五个评分维度逐条映射到流程与自检项，详见 `references/rubric-alignment.md`，`checks.py` 内置同表并在 `selfcheck` 校验覆盖：

- **实际效果(40)** → 阶段2/5 + 六项守恒检查（覆盖/数字单位/引用/图表/限定强度/溯源），`errors==0` 才算通过。
- **使用增益(25)** → 阶段3/4 + `gain_metrics`：由 `edits.json`+`check_report.json` 公开推导（`total_units/edited_units/edit_types/hedge_notes_written/errors_caught/evidence_entries`），供与基础 Agent 同输入对照，不依赖隐藏白板。
- **创新增益(15)** → 阶段3 双通道（编辑→独立回读）+ 限定强度守恒与术语回扫，记录在 `change_log`，可独立复现。
- **可靠体验(10)** → 阶段0/6 + `deterministic_scaffold`/`selfcheck_ok`，`selftest.py` 覆盖空目录/坏 JSON/缺输入/超长/坏编码五场景。
- **生态贡献(10)** → 全程 + `references_present`/`stdlib_only`，术语表/风格规则/评分表可直接复用。

## 6. 自检清单（逐条对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法（可靠体验）。
- [ ] output_contract 全部字段已回填，未知键未删（完整性）。
- [ ] 交付"润色全文 + 逐句修改表"，而非建议或整段重写（任务完成度）。
- [ ] `verify` 报告 `errors == 0`：数字/单位/引用/图表指向/覆盖/溯源无缺失（正确性）。
- [ ] 每条 hedge 警告已"回退或写 `hedge_note`"，无静默加强/削弱结论（语义保持）。
- [ ] 术语表建全：同一概念全文唯一写法；缩写首次出现已给全称（术语一致性）。
- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有 locator + 逐字 quote（可核验）。
- [ ] `gain_metrics` 已生成且来自公开产物，未杜撰分数（增益可验证）。
- [ ] 材料缺口写 `MATERIAL GAP` / `pending_human` / `not_reported`，未编造数据或分数。
- [ ] `selfcheck` 返回 OK，`references/*.md` 存在，脚本仅标准库（生态贡献）。

## 7. 降级策略（缺数据/缺软件必须如实标注，不编造）

- **空输入/无底稿**：`status=insufficient_evidence`，退出码 0，列缺口，不写示例内容。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **PDF/Office 二进制**：记入 `sources` 标 `opaque`；无解析工具时标 `pending_human`，不按文件名猜内容。
- **缺渲染软件**：交付可编辑 `.md`/`.json` 主产物，并在 `degradations` 注明"未生成 PDF/PNG 预览"，不伪造 PDF。
- **材料冲突**：两个来源不一致时全部记录并标 `source_conflict`，不静默选边。
- **文体/受众不明**：用最保守的最小编辑（只改硬语法错误、不改限定词），并在 `uncertainty` 标注。
- **超长输入**：按章节分批处理，每句一个 unit；单文件超限（>2MB）跳过并记录。

工具：`scripts/main.py`（scaffold/verify/finalize/selfcheck，标准库）、`scripts/checks.py`（确定性校验，可复用）、`scripts/selftest.py`（五场景自检）。规则见 `references/preservation-rules.md`；评分维度映射见 `references/rubric-alignment.md`。
