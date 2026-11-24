---
name: academic-polish-03
description: 学术语言润色与保真复核 Skill（时间预算与早落盘版）。对论文、摘要、基金稿、审稿回复、学位论文等科研文本做最小编辑（语法、术语、语域、语义保持、中英双语），逐句产出"原文—改文—修改类型—理由—证据"，并用确定性脚本校验数值、单位、引用、图表指向、限定强度与术语一致性是否被改坏。前 3 分钟先落盘 partial 结果骨架，之后按 900 秒 / 64 请求的阶段预算增量改进。当需要润色或改写学术文本且必须保留事实与来源时使用。
version: 1.0.0
---

# 学术语言润色与保真复核 Skill（时间预算与早落盘版）

## 1. 角色与何时使用

本 Skill 让 Agent 在**改写学术文本的同时完成保真复核**，交付"润色后完整文本＋逐句修改说明"，而不是写作建议或整段重写。

- **使用**：输入给出事实底稿/稿件/摘要，要求润色、改写、校对、术语统一、中英互译，或按期刊论文、学位论文、基金申请书、审稿回复、英文摘要调整语域时。
- **不使用**：纯内容问答、生成新实验数据、解释概念、写全新稿件（无底稿）。此时不触发，避免擅自改写。
- **唯一硬约束**：只改"表达"，不改"事实与强度"。限定词强度、数据、单位、引用编号、图表指向、因果与相关、否定范围、条件句、量词必须原样保留；确需变更必须写入 `version_boundary` 并标 `pending_human`。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，不假设固定文件名。扫描并识别：

- `/input/case.json`：任务与材料索引；可能含 `output_contract`、`id`/`case_id`、内联 `text` 字段。
- `/input/task.md`：任务说明、受众、文体、格式约束、评分要求。
- 材料：`/input/materials/` 或根目录下 `*.md`、`*.txt`、`*.tex`、`*.pdf`、`*.docx` 等。PDF 用 pdftotext/OCR 解析**真实文件**，不依据文件名补造内容。

文本一律按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级而不崩溃）；单文件 >2MB 跳过并记录。

## 3. 输出契约（写到哪里）

最终产物写入 `/app/output/`，工作中间态写入 `/app/work/`。必须产出：

- `/app/output/result.json`（必需）：`case_id`＋题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）＋`status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`。
- 默认 output_contract 字段：`deliverable`（可编辑主产物路径）、`fact_source_map`（每项含 `fact`/`locator`/`quote`）、`version_boundary`（差异、人工确认项、缺口）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，诚实标注；`sources` 带路径/类型/字节/摘要指纹。
- `/app/output/manuscript.md`（可编辑正稿：润色全文＋逐句修改表）、`/app/output/glossary.md`（若有术语表）。Office/slide/diagram 类产物必须另附真实渲染的 PDF/PNG 预览；无法渲染时如实说明，不伪造 PDF。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> **铁律：前 3 分钟内必须落盘一版 result.json 骨架，之后只做增量改进。** `scaffold` 是确定性、零模型调用的早落盘步骤，任何中途失败都不留空产物。

| 阶段 | 时间 | 请求 | 动作 | 输入→输出 |
|---|---|---|---|---|
| 0 scaffold | 0–40s | 0 | `python3 -B scripts/main.py scaffold` | `/input` → `/app/output/result.json`(partial)＋`/app/work/edits.json`(模板)＋`evidence_index.json`(事实池)＋`phase_ledger.json`(预算台账) |
| 1 读材料 | 40–180s | 8 | 读 task.md、case.json、材料；建术语表与事实池；定 `target_lang`/`genre` | 材料 → `glossary.json`、`edits.json.meta` |
| 2 逐句编辑 | 180–420s | 28 | 按句填 `units[]`：`original/revised/edit_type/reason/evidence`；只改表达不改事实 | 底稿 → 完整逐句编辑记录 |
| 3 双通道审校 | 420–600s | 14 | 先编辑后独立复核（对照原文回读/回译）；运行 `python3 -B scripts/main.py verify` | `edits.json` → `check_report.json` |
| 4 修复 | 600–760s | 12 | 清零所有 error（数字/单位/引用/覆盖/溯源）；hedge 警告逐一"回退或写理由" | `check_report` → 修订 `edits.json` |
| 5 定稿 | 760–820s | 0 | `python3 -B scripts/main.py finalize` | → `manuscript.md`＋`glossary.md`＋完整 `result.json` |
| 6 自检 | 820–900s | 2 | `python3 -B scripts/main.py selfcheck` | 校验 result.json 与产物 |

- 脚本从 Skill 根目录调用（相对路径 `scripts/main.py`），缺省 `--input /input --output /app/output --work /app/work`；也可显式传参。
- `scaffold` 用坏 JSON/空目录/坏编码的降级逻辑写好合法 partial 骨架，并在 `/app/work/phase_ledger.json` 记录 900s/64 请求的阶段预算与早落盘时刻。
- 每阶段结束可运行 `python3 -B scripts/main.py ledger --mark <阶段号>` 更新台账，保持剩余预算可见。
- 同一输入重复运行结果一致：校验逻辑确定性、无随机数；`runtime` 仅作测量元数据。

## 5. 自检清单（逐条对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法（可靠性）。
- [ ] output_contract 全部字段已回填，未知键未删（完整性）。
- [ ] 交付"润色全文＋逐句修改表"，而非建议或整段重写（任务完成度）。
- [ ] `python3 -B scripts/main.py verify` 报告 `errors == 0`：数字/单位/引用/图表指向/覆盖/溯源无缺失（正确性）。
- [ ] 每条 hedge 警告已"回退或显式写理由"，无静默加强/削弱结论（语义保持）。
- [ ] 术语表建全：同一概念全文唯一写法；缩写首次出现已给出全称（术语一致性）。
- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有 locator＋逐字 quote（可核验）。
- [ ] 材料缺口写 `MATERIAL GAP` / `pending_human` / `not_reported`，未编造数据或分数。
- [ ] `python3 -B scripts/main.py selfcheck` 返回 OK，产物文件存在且可编辑。

## 6. 降级策略（缺数据/缺软件必须如实标注，不编造）

- **空输入/无底稿**：`status=insufficient_evidence`，退出码 0，列缺口，不写示例内容。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **PDF/Office 二进制**：记入 `sources` 标 `opaque`；无解析工具时标 `pending_human`，不得按文件名猜内容。
- **缺渲染软件**：交付可编辑 `.md`/`.json` 主产物，并在 `degradations` 注明"未生成 PDF/PNG 预览"，不伪造 PDF。
- **材料冲突**：两个来源不一致时全部记录并标 `source_conflict`，不静默选边。
- **文体/受众不明**：用最保守的最小编辑（只改硬语法错误、不改限定词），并在 `uncertainty` 标注。
- **超长输入**：按章节分批处理，每句一个 unit；单文件超限跳过并记录。

## 7. 最小调用示例（示例，非正式案例）

```bash
python3 -B scripts/main.py scaffold          # 早落盘 partial 骨架
# …Agent 填 /app/work/edits.json、glossary.json…
python3 -B scripts/main.py verify            # 确定性校验，errors 清零后继续
python3 -B scripts/main.py finalize          # 组装 manuscript.md / glossary.md / result.json
python3 -B scripts/main.py selfcheck         # 交付前自检，期望返回 OK
```

自检脚本：`python3 -B scripts/selftest.py`（或 `python3 -B scripts/main.py selftest`），在临时目录内跑通 scaffold→verify→finalize→selfcheck、破坏性样例与空输入降级，期望输出 `selftest PASS`。规则详见 `references/preservation-rules.md` 与 `references/review-checklist.md`。
