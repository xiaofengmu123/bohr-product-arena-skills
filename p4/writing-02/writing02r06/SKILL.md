---
name: academic-polish-invariants-02
description: 学术语言润色与语义不变量守恒复核。对论文/摘要/基金稿/审稿回复/学位论文等科研文本做最小编辑（语法、术语、语域、语义保持、中英双语），先抽取"语义不变量注册表"（数字/单位/限定强度/引用/图表指向/量词/否定/条件/因果-相关/术语）作为结构化中间产物，逐句产出"原文—改文—修改类型—理由—不变量守恒证明"，再用确定性脚本校验没有任何不变量被改坏。当需要润色或改写学术文本且必须保留事实与来源时使用。
version: 1.0.0
---

# 学术语言润色与语义不变量守恒 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 在改写学术文本的同时完成**可机器复核的保真校验**，交付“润色全文＋逐句修改表＋不变量守恒证明”，而不是写作建议或整段重写。

- **使用**：输入给出事实底稿/稿件/摘要，要求润色、改写、校对、术语统一、中英互译，或按体裁（期刊论文、学位论文、基金申请书、审稿回复、英文摘要）调整语域时。
- **不使用**：纯内容问答、生成新实验数据、解释概念、写全新稿件（无底稿）——此时不触发，避免擅自改写。
- **唯一硬约束**：润色只改“表达”，不改“事实与强度”。数字、单位、引用编号、图表指向、限定强度、因果与相关、否定范围、条件句、量词必须原样保留；每一处修改都要有原文、改文、修改类型、理由与不变量守恒证明。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，不假设固定文件名。扫描并识别：

- `/input/case.json`：任务与材料索引；可能含 `output_contract`、`id`/`case_id`、内联 `text`。
- `/input/task.md`：任务说明、受众、文体、格式约束、评分要求。
- 材料：`/input/materials/` 或根目录下的 `*.md`、`*.txt`、`*.tex`、`*.pdf`、`*.docx` 等。PDF 用 pdftotext/OCR 解析**真实文件**，不按文件名补造内容。

文本一律按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记录。正式案例、白板与测评结果由平台提供，不自行编造。

## 3. 输出契约（写到哪里）

最终产物写入 `/app/output/`，中间态写入 `/app/work/`。必须产出：

- `/app/output/result.json`（必需）：`case_id` + 题目 `output_contract` 的**全部字段原样回填**（缺值给 `null`/`[]`，不改名、不删未知键）+ `status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `errors` / `degradations` / `runtime`。
- 默认 output_contract：`deliverable`（可编辑主产物路径）、`fact_source_map`（每项 `fact`/`locator`/`quote`）、`version_boundary`（`type`/`detail`/`status`）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，诚实标注；`sources` 带路径/类型/字节/SHA 指纹。
- `/app/output/manuscript.md`（润色全文＋逐句修改表＋术语表＋缺口/冲突）、`/app/output/glossary.md`（若有术语表）。
- 中间产物（本 Skill 的核心设计，写入 `/app/work/` 并登记到 `sources`）：`plan.json`（不变量注册表）、`ledger.json`（逐句编辑台账）、`check_report.json`（确定性校验报告）。详见 `references/audit-protocol.md`。
- Office/slide/diagram 类产物必须另附真实渲染的 PDF/PNG 预览；无法渲染时如实说明，不伪造 PDF。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求）

> 铁律：**前 3 分钟内必须落盘一版 result.json 骨架**（`scaffold` 立即写 partial），之后只做增量改进，任何中途失败都不留空产物。

| 阶段 | 时间 | 动作 | 输入→输出 | 模型请求 |
|---|---|---|---|---|
| 0 脚手架 | 0–15s | `python3 -B scripts/main.py scaffold` | `/input` → partial `result.json` + `plan.json`/`ledger.json` 骨架 | 0 |
| 1 读材料建注册表 | 15–150s | 读 task.md/case.json/材料；定 `target_lang`/`genre`；抽不变量（数字/单位/引用/限定强度…逐条带 locator+quote）与术语表 | 材料 → `plan.json` 的不变量与术语 | 3–6 |
| 2 逐句编辑 | 150–480s | 按句填 `ledger.json` 的 `units[]`：`original/revised/edit_type/reason/touched_invariants/conservation` | 底稿 → 完整逐句台账 | 20–30 |
| 3 双通道审校 | 480–660s | 先编辑、后独立回读/回译；`python3 -B scripts/main.py verify` | `plan.json`+`ledger.json` → `check_report.json` | 10–15 |
| 4 修复 | 660–780s | 清零所有 `error`（数字/单位/引用/覆盖/溯源缺失）；hedge 警告逐一“回退或写 `explicit_reason`” | `check_report` → 修订 `ledger.json` | 5–10 |
| 5 定稿 | 780–840s | `python3 -B scripts/main.py finalize` | → `manuscript.md` + `glossary.md` + 完整 `result.json` | 0 |
| 6 自检 | 840–900s | `python3 -B scripts/main.py selfcheck`；`python3 -B scripts/selftest.py` | 校验 result.json 与产物 | 0 |

- 脚本从 Skill 根目录以相对路径调用；缺省 `--input /input --output /app/output --work /app/work`，也可显式传参。
- 同一输入重复运行结果一致：校验逻辑确定性、无随机数、无网络；`runtime` 仅作测量元数据。

## 5. 自检清单（逐条对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法（可靠性）。
- [ ] output_contract 全部字段已回填，未知键未删（完整性）。
- [ ] 交付“润色全文＋逐句修改表”，而非建议或整段重写（任务完成度）。
- [ ] `verify` 报告 `errors == 0`：数字/单位/引用/图表指向/覆盖/溯源无缺失（正确性）。
- [ ] 每条 hedge 警告已“回退或写 `explicit_reason`”，无静默加强/削弱（语义保持）。
- [ ] 术语表建全：同一概念全文唯一写法；缩写首次出现已给全称（术语一致性）。
- [ ] `fact_source_map` 覆盖所有带数字/引用/关键论断的句子，逐条有 `locator` + 逐字 `quote`（可核验）。
- [ ] 材料缺口写 `MATERIAL GAP` / `pending_human` / `not_reported`，未编造数据或分数。
- [ ] `selfcheck` 返回 OK，`selftest` 全 PASS，产物文件存在且可编辑。

## 6. 降级策略（缺数据/缺软件必须如实标注，不编造）

- **空输入/无底稿**：`status=insufficient_evidence`，退出码 0，列缺口，不写示例内容。
- **坏 JSON/坏编码**：记入 `errors`/`degradations`，继续处理其余材料，不崩溃。
- **PDF/Office 二进制**：`sources` 标 `opaque`；无解析工具时标 `pending_human`，不得按文件名猜内容。
- **缺渲染软件**：交付可编辑 `.md`/`.json` 主产物，并在 `degradations` 注明“未生成 PDF/PNG 预览”，不伪造 PDF。
- **材料冲突**：两个来源不一致时全部记录并标 `source_conflict`，不静默选边。
- **文体/受众不明**：用最保守最小编辑（只改硬语法错误、不改限定词），并在 `uncertainty` 标注。
- **超长输入**：按章节分批处理，每句一个 unit；单文件超限跳过并记录。

## 7. 最小调用示例（示意，非正式案例结果）

```bash
# 运行时（平台自动执行）
python3 -B scripts/main.py scaffold          # 0–15s 先落 partial 骨架
# Agent 读材料、填 /app/work/plan.json 与 /app/work/ledger.json
python3 -B scripts/main.py verify            # 确定性守恒校验
python3 -B scripts/main.py finalize          # 组装 manuscript.md + result.json
python3 -B scripts/main.py selfcheck         # 终检
python3 -B scripts/selftest.py               # 自检脚本（本地复现用）
```

示意输入（示意）：`/input/materials/draft.md` 含一句 “The result suggest that treatment improve the outcome (n=128) [12].”；示意产物 `manuscript.md` 给出“The results suggest that treatment improved the outcome (n=128) [12].” 并在逐句表标注修改类型 `grammar`、理由与不变量守恒证明。**以上为示例，不冒充正式案例结果。**

工具：`scripts/main.py`（scaffold/verify/finalize/selfcheck，标准库）、`scripts/invariants.py`（确定性校验引擎）、`scripts/selftest.py`（自检）。规则详见 `references/preservation-rules.md` 与 `references/audit-protocol.md`。
