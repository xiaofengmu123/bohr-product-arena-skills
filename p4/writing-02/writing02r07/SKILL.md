---
name: academic-polish-robust-02
description: 学术语言润色与保真复核（鲁棒性强化版）。对论文/摘要/基金稿/审稿回复/学位论文等科研文本做最小编辑（语法、术语、语域、语义保持、中英双语），逐句产出"原文—改文—修改类型—理由"，并用确定性脚本校验限定强度、数字单位、引用与图表指向、术语一致性是否被改坏。空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级不崩溃、不留空产物。当需要润色或改写学术文本且必须保留事实与来源时使用。
version: 1.0.0
---

# 学术语言润色与保真复核 Skill（鲁棒性强化版）

## 1. 角色与何时使用

本 Skill 让 Agent 在**改写学术文本的同时完成保真复核**，交付"润色后的完整文本＋逐句修改说明"，而不是一段写作建议或整段重写。

- **使用**：输入给出事实底稿/稿件/摘要，要求润色、改写、校对、术语统一、中英互译，或按体裁（期刊论文、学位论文、基金申请书、审稿回复、英文摘要）调整语域。
- **不使用**：纯内容问答、生成新实验数据、解释概念、写全新稿件（无底稿）。此时不触发，避免擅自改写。
- **唯一硬约束**：润色只改"表达"，不改"事实与强度"。限定词强度、数据、单位、引用编号、图表指向、因果与相关、否定范围、条件句、量词必须原样保留。细则见 `references/preservation-rules.md`。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，**不要假设固定文件名**。扫描并识别：

- `/input/case.json`：任务与材料索引；可能含 `output_contract`、`id`/`case_id`、内联 `text` 字段。
- `/input/task.md`：任务说明、受众、文体、格式约束、评分要求。
- 材料：`/input/materials/` 或根目录下的 `*.md`、`*.txt`、`*.tex`、`*.pdf`、`*.docx` 等。PDF/Office 用 pdftotext/OCR 解析**真实文件**，不依据文件名补造内容。

文本一律按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记录。所有识别与解析逻辑集中在 `scripts/main.py` 的 `discover`/`find_draft`，可复用。

## 3. 输出契约（写到哪里）

最终产物写入 `/app/output/`，工作中间态写入 `/app/work/`。必须产出：

- `/app/output/result.json`（必需）：`case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）+ `status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`。
- 默认 `output_contract` 字段：`deliverable`（可编辑主产物路径）、`fact_source_map`（每项含 `fact`/`locator`/`quote`）、`version_boundary`（差异、人工确认项、缺口）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，诚实标注；`sources` 带路径/类型/字节/摘要指纹。
- `/app/output/manuscript.md`（可编辑正稿：润色全文 + 逐句修改表）、`/app/output/glossary.md`（若有术语表）。Office/slide/diagram 类产物必须另附真实渲染的 PDF/PNG 预览；无法渲染时如实说明，不伪造 PDF。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> 铁律：**前 3 分钟内必须落盘一版 `result.json` 骨架**（`scaffold` 已在 0 请求时完成），之后只做增量改进；任何中途失败都不留空产物。

| 阶段 | 时间 | 动作 | 输入→输出 | 模型请求 |
|---|---|---|---|---|
| 0 脚手架 | 0–15s | `python3 -B scripts/main.py scaffold` | `/input` → `result.json`(partial) + `edits.json`(逐句模板) + `evidence_index.json` + `glossary.json` | 0 |
| 1 读材料 | 15–150s | 读 task.md、case.json、材料；建证据索引与术语表；定 `target_lang`/`genre` | 材料 → 事实池（数字/单位/引用/限定词逐条带 locator+quote） | 3–6 |
| 2 逐句编辑 | 150–480s | 按句填 `edits.json` 的 `units[]`：`original/revised/edit_type/reason/evidence`；只改表达不改事实 | 底稿 → 完整逐句编辑记录 | 20–30 |
| 3 双通道审校 | 480–660s | 先编辑、后独立复核（对照原文回读/回译）；运行 `python3 -B scripts/main.py verify` | `edits.json` → `check_report.json` | 10–15 |
| 4 修复 | 660–780s | 清零所有 error（数字/单位/引用/覆盖/溯源缺失）；对 hedge 警告逐一"回退或写明理由" | `check_report` → 修订 `edits.json` | 5–10 |
| 5 定稿 | 780–840s | 运行 `python3 -B scripts/main.py finalize` | → `manuscript.md` + `glossary.md` + 完整 `result.json` | 0 |
| 6 自检 | 840–900s | 运行 `python3 -B scripts/main.py selfcheck` | 校验 result.json 与产物 | 0 |

- 脚本从 Skill 根目录调用（相对路径 `scripts/main.py`），缺省 `--input /input --output /app/output --work /app/work`；也可显式传参。
- 同一输入重复运行结果一致：校验逻辑确定性、无随机数；`runtime` 仅作测量元数据。

## 5. 自检清单（逐条对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法（可靠性）。
- [ ] output_contract 全部字段已回填，未知键未删（完整性）。
- [ ] 交付"润色全文 + 逐句修改表"，而非建议或整段重写（任务完成度）。
- [ ] `verify` 报告 `errors == 0`：数字/单位/引用/图表指向/覆盖/溯源无缺失（正确性）。
- [ ] 每条 hedge 警告已"回退或显式写理由"，无静默加强/削弱（语义保持）。
- [ ] 术语表建全：同一概念全文唯一写法；缩写首次出现已给全称（术语一致性）。
- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有 locator + 逐字 quote（可核验）。
- [ ] 材料缺口写 `MATERIAL GAP` / `pending_human` / `not_reported`，未编造数据或分数。
- [ ] `selfcheck` 返回 OK，产物文件存在且可编辑。
- [ ] `python3 -B scripts/selftest.py` 全通过（鲁棒性自检）。

完整逐条清单见 `references/rubric-checklist.md`。

## 6. 降级策略（缺数据/缺软件必须如实标注，不编造）

- **空输入/无底稿**：`status=insufficient_evidence`，退出码 0，列缺口，不写示例内容。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **超大文件**：跳过并记入 `sources` 标 `skipped_oversized`；不按文件名猜内容。
- **PDF/Office 二进制**：记入 `sources` 标 `opaque`；无解析工具时标 `pending_human`。
- **缺渲染软件**：交付可编辑 `.md`/`.json` 主产物，`degradations` 注明"未生成 PDF/PNG 预览"，不伪造 PDF。
- **材料冲突**：两个来源不一致时全部记录并标 `source_conflict`，不静默选边。
- **文体/受众不明**：用最保守的最小编辑（只改硬语法错误、不改限定词），`uncertainty` 标注。
- **超长输入**：按章节分批处理，每句一个 unit；单文件超限跳过并记录。
- **任何内部异常**：`scaffold` 捕获后写最小合法 `result.json`（`status=input_error`），退出码 1，不留空产物。

## 7. 脚本与资源

- `scripts/main.py`（scaffold/verify/finalize/selfcheck，标准库，<30KB）
- `scripts/checks.py`（确定性保真校验，可复用）
- `scripts/selftest.py`（鲁棒性自检：空输入/坏 JSON/坏编码/超大文件/缺参数/确定性）
- `references/preservation-rules.md`（保真规则）
- `references/rubric-checklist.md`（自检清单）
- `references/example.md`（最小调用示例，标为示例）

全部标准库、无网络、无随机数；Python ≥3.8。
