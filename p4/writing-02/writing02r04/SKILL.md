---
name: academic-polish-02
description: 学术语言润色与保真复核（降级与不确定性显式版）。对论文/摘要/基金稿/审稿回复/学位论文等科研文本做最小编辑，逐句产出"原文—改文—修改类型—理由—置信度"，并用确定性脚本校验限定强度、数字单位、引用与图表指向、术语一致性；显式记录 confidence/certainty/limitations/uncertainty 与材料缺口，缺数据不编造。适用于需要保留事实与来源的润色、改写、校对、术语统一与中英双语任务。
version: 2.0.0
---

# 学术语言润色与保真复核 Skill（不确定性显式版）

## 1. 角色与何时使用

本 Skill 让 Agent 在**改写学术文本的同时完成保真复核与不确定性标注**，交付
「润色后完整文本 ＋ 逐句修改表 ＋ 术语表 ＋ 可回溯的 fact_source_map」，而不是
一段写作建议或一次整段重写。

- **使用**：输入给出事实底稿/稿件/摘要，要求润色、改写、校对、术语统一、中英互译，
  或按体裁（期刊论文、学位论文、基金申请书、审稿回复、英文摘要）调整语域时。
- **不使用**：纯内容问答、生成新实验数据、解释概念、写全新稿件（无底稿）。此时不触发。
- **唯一硬约束**：润色只改"表达"，不改"事实与强度"。数字、单位、引用编号、图表指向、
  限定词强度、因果与相关、否定范围、条件句、量词必须原样守恒（详见
  `references/preservation-rules.md`）。
- **本版侧重点**：降级与不确定性显式化——每处修改带置信度，每条事实带确定性，
  每个缺口进受控词表，缺数据写 `MATERIAL_GAP`/`pending_human`/`not_reported`，
  绝不编造（详见 `references/uncertainty-rules.md`）。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，不假设固定文件名。先扫描：

- `/input/case.json`：任务与材料索引；可能含 `output_contract`、`id`/`case_id`、内联 `text`。
- `/input/task.md`：任务说明、受众、文体、格式约束、评分要求。
- 材料：`/input/materials/` 或根目录下 `*.md`、`*.txt`、`*.tex`、`*.pdf`、`*.docx` 等。
  PDF 用 pdftotext/OCR 解析**真实文件**，不依据文件名补造内容。

文本一律按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件 >2MB
跳过并记录。`scripts/main.py scaffold` 会自动发现底稿与材料，写入证据索引。

## 3. 输出契约（写到哪里）

最终产物写入 `/app/output/`，中间态写入 `/app/work/`。必须产出：

- `/app/output/result.json`（必需）：
  - `case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，
    不改名、不删未知键）；
  - `status`（`ok|partial|insufficient_evidence|input_error`）；
  - `sources`（路径/类型/字节/sha 摘要指纹）、`uncertainty`、`limitations`、
    `pending_human`、`change_log`、`errors`、`degradations`、`runtime`。
- `/app/output/manuscript.md`：可编辑正稿（润色全文 + 逐句修改表）。
- `/app/output/glossary.md`：术语表（若有）。
- Office/slide/diagram 类产物必须另附真实渲染的 PDF/PNG 预览；无法渲染时在
  `degradations` 如实注明，不伪造 PDF。

默认 `output_contract` 字段：`deliverable`（可编辑主产物路径）、`fact_source_map`
（每项 `fact`/`locator`/`quote`）、`version_boundary`（差异、人工确认项、缺口）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> **铁律：前 3 分钟内必须落盘一版 result.json 骨架**，之后只做增量改进；
> 任何中途失败都不留空产物。状态由脚本确定性推导，Agent 不手写状态。

| 阶段 | 时间 | 动作 | 输入→输出 | 请求 |
|---|---|---|---|---|
| 0 脚手架 | 0–15s | `python3 -B scripts/main.py scaffold` | `/input` → partial `result.json` + `/app/work/edits.json` 逐句模板 | 0 |
| 1 读材料 | 15–150s | 读 task.md、case.json、材料；建 `glossary`、事实池（数字/单位/引用/限定词逐条带 locator+quote） | 材料 → `/app/work/edits.json` 的 `facts[]/glossary[]/gaps[]` | 3–6 |
| 2 逐句编辑 | 150–480s | 填 `units[]`：`original/revised/edit_type/reason/confidence/uncertainty_reason`；只改表达不改事实 | 底稿 → 完整逐句编辑记录 | 20–30 |
| 3 双通道审校 | 480–660s | 先编辑、后独立复核（对照原文回读/回译）；`python3 -B scripts/main.py verify` | `edits.json` → `check_report.json` | 10–15 |
| 4 修复 | 660–780s | 清零 error（数字/单位/引用/覆盖/溯源缺失）；对 hedge 警告逐一"回退或写 `hedge_justified` + 理由" | `check_report` → 修订 `edits.json` | 5–10 |
| 5 定稿 | 780–840s | `python3 -B scripts/main.py finalize` | → `manuscript.md` + `glossary.md` + 完整 `result.json` | 0 |
| 6 自检 | 840–900s | `python3 -B scripts/main.py selfcheck` | 校验 result.json 与产物存在性 | 0 |

- 脚本从 Skill 根目录调用（相对路径 `scripts/main.py`）；缺省 `--input /input
  --output /app/output --work /app/work`，也可显式传参。
- `scaffold` 会先用坏 JSON/空目录/坏编码的降级逻辑写好合法 partial 骨架，保证
  第 3 分钟前有可交付物。
- 同一输入重复运行结果一致：校验逻辑确定性、无随机数；`runtime` 仅作测量元数据。

## 5. 自检清单（逐条对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空（`input_error` 除外），`status` 合法（可靠性）。
- [ ] output_contract 全部字段已回填，未知键未删（完整性）。
- [ ] 交付"润色全文 + 逐句修改表"，而非建议或整段重写（任务完成度）。
- [ ] `verify` 报告 `errors == 0`：数字/单位/引用/图表指向/覆盖/溯源无缺失（正确性）。
- [ ] 每条 hedge 警告已"回退或显式 `hedge_justified` + 理由"，无静默加强/削弱（语义保持）。
- [ ] 术语表建全：同一概念全文唯一写法；缩写首次出现已给全称（术语一致性）。
- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有 locator + 逐字 quote（可核验）。
- [ ] 每处修改带 `confidence`，每条事实带 `certainty`，低置信/未核实已进 `uncertainty`（不确定性显式）。
- [ ] 材料缺口写 `MATERIAL_GAP` / `pending_human` / `not_reported`，未编造数据或分数。
- [ ] `limitations` 列明二进制源/缺口/未解决警告等已知边界。
- [ ] `selfcheck` 返回 OK；`python3 -B scripts/selftest.py` 全场景通过（空输入/坏 JSON/坏编码/无底稿/正常）。

## 6. 降级策略（缺数据/缺软件如实标注，不编造）

- **空输入/无底稿**：`status=input_error` 或 `insufficient_evidence`，退出码 0，列缺口，不写示例内容。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **PDF/Office 二进制**：`sources` 标 `opaque`；无解析工具标 `pending_human`，不得按文件名猜内容。
- **缺渲染软件**：交付可编辑 `.md`/`.json` 主产物，`degradations` 注明"未生成 PDF/PNG 预览"，不伪造 PDF。
- **材料冲突**：两来源不一致全部记录并标 `source_conflict`，不静默选边。
- **文体/受众不明**：最保守的最小编辑（只改硬语法错误、不改限定词），并在 `uncertainty` 标注。
- **超长输入**：按章节分批，每句一个 unit；单文件 >2MB 跳过并记录。
- **低置信**：无法判断该不该改时，保留原文并标记，不给出看似流畅但可能改错的版本。

## 7. 工具与文件

- `scripts/main.py`（scaffold/verify/finalize/selfcheck，纯标准库，确定性）。
- `scripts/checks.py`（确定性守恒校验，可复用）。
- `scripts/selftest.py`（自检：空目录、坏 JSON、坏编码、无底稿、完整流水线）。
- `references/preservation-rules.md`（保真规则与 schema）。
- `references/uncertainty-rules.md`（降级与不确定性规则、状态阶梯、缺口词表）。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
python3 -B scripts/main.py scaffold
# Agent 依据 /input 材料填写 /app/work/edits.json 的 units/facts/glossary/gaps
python3 -B scripts/main.py verify
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
```

示意输出（标注为示例）：

```json
{"case_id": "demo", "status": "ok", "deliverable": "manuscript.md",
 "uncertainty": {"summary": "0 errors", "items": []}}
```
