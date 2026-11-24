---
name: source-bound-academic-audit
description: 对科研论文或结题材料做投稿前/发布前的逐条学术规范与合规审查（学术诚信、引用规范、隐私与数据合规、版权与许可、伦理审查、投稿要求，以及质量/统计/方法/逻辑）。每条问题必须指回原文具体位置（file + locator + 逐字 quote）、给出判定依据、严重度与可执行修改建议，并区分事实错误、证据不足、规范问题与建议。适合需要可复核、可复跑的审查报告时使用；不代写、不润色、不重做数据分析，不出具法务或伦理委员会正式意见。
version: 1.1.0
---

# 来源绑定的学术规范与合规审查 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 把一份科研材料审查成**逐条、可复核**的结论：报出的每条问题都能回查原文位置，每条判定都有可核对的依据（规范条款、期刊要求、授权范围原文），并明确严重度与修改动作；不产出"建议检查格式是否规范"这类泛泛提醒。

适用：投稿前、结题前、答辩/发布前的质量与合规把关；需要相对比较"加载/不加载 Skill"的实际产出时。

不适用：润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、可能的 `output_contract`）、`/input/task.md`（题面）、`/input/` 下的材料（PDF、md、txt、tex、csv、bib 等）。
- **必须读实际文件，不依据标签猜内容**。PDF 用 `pdftotext`（或平台的 pdf-text-tool）解析到 `/app/work/parsed/<原名>.txt`，再交给脚本建索引；只覆盖已解析文本，缺页/无文本如实记录。
- 工作目录 `/app/work/`；最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码逐级降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录到 degradations。
- 运行预算：**900 秒 / 64 次模型请求 / 每次含推理 16384 tokens**。预算不足时交付 partial 并写清缺口，不硬撑。

## 3. 输出契约

必须产出 `/app/output/result.json`，字段如下：

- `case_id`：取自 `case.json` 的 `case_id`/`id`/`challenge_id`/`task_id`；缺失用文件名并记录。
- `output_contract`：**原样回填 `case.json` 中 output_contract 的全部键**（缺值给 `null`/`[]`，不改名、不删未知键）；同时把非核心叶子字段平铺到顶层，但不覆盖核心审计键。缺 `output_contract` 时用标准 schema 并标 `contract_missing=true`。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实计算：无解析材料且无结论 → `insufficient_evidence`；六方向未全查或有 critical/major → `partial`；全查且无 critical/major → `ok`。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`。
- `report.checked_items[]`：六方向（学术诚信、引用、隐私、版权、伦理、投稿）+ 质量/统计/方法/逻辑，逐项标 `checked/pending/unchecked`，禁止只查最省事的一条。
- `sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`runtime` 均为数组/对象且至少 `uncertainty`、`change_log` 非空。
- 同步产出 `/app/output/report.md`（可编辑报告），并在 `result.json` 中记录其路径。

## 4. 执行流程（总 900 秒、64 次请求）

| 步骤 | 时间 | 输入 → 输出 | 动作 |
|---|---|---|---|
| 0 确认契约 | 0–1 min | case.json + task.md → 记录 case_id、output_contract、检查范围、材料清单 | 读实际文件 |
| 1 骨架落盘 | 0–3 min | `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work` → result.json 骨架（status=partial）+ report.md + plan.md + evidence.json | **此后任何中断都已有合法产物** |
| 2 逐条审查 | 3–20 min | 读 `evidence.json` 候选 + 原文 → 判定成立/排除/调严重度，按六方向补查 | 每条写全四要素：位置+quote、依据、严重度、修改建议 |
| 3 增量更新 | 随步骤 2 | 把 findings 写入 `/app/work/findings.json` → `main.py merge` 更新 result.json + report.md | 每次改动追加 change_log；merge 幂等 |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/main.py validate ...` → 修 FAIL | 预算不足时如实 partial，不编造 |

原则：前 3 分钟先落盘可解析骨架；之后只做**增量改进**，任何时刻 result.json 保持合法 JSON。候选观察（observation）不是结论，必须逐条判定后写入 findings。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范/合规问题）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 四要素缺一不可：原文位置（file + locator + quote）、判定依据（basis，写条款/指南/授权范围原文及版本日期）、严重度、可执行修改建议（revision）。
- 误报抑制：风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`；受限数据只标位置与类型，不复述内容。
- 引用核对：DOI/PMID/arXiv 要能对上被引文献，不能只凭正文里的编号猜；拿不到元数据就写"无法核实"。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空（任务完成度/可核验性）
- [ ] `status` 诚实且 ∈ 允许集合（正确性/异常处理）
- [ ] `output_contract` 全部键存在，缺值 null/[]，未改名未删键（结果完整性）
- [ ] 每条 finding 含全部必需字段，类型/严重度/分类合法（可核验性）
- [ ] 每条 `quote` 能回查原文（`validate.py` 通过；对未解析文件标 pending_human）
- [ ] 六方向逐项有 checked/pending/unchecked 结论，未只挑一条（结果完整性）
- [ ] 误报已排除或降级；冲突标 source_conflict（正确性）
- [ ] `sources/uncertainty/change_log` 非空；change_log 记录每次增量（生态贡献）
- [ ] 材料缺失/解析失败/软件缺失已写入 errors/degradations（异常处理）
- [ ] `report.md` 可读，result.json 记录其路径（调用与易用性）
- [ ] 同一输入重跑结论一致（脚本确定性；`scripts/selftest.py` 全绿）（可靠体验）

## 7. 降级策略（详见 references/audit-rules.md）

- 缺 PDF 文本：依次 pdftotext → 平台 pdf-text-tool → OCR；仍不可得 → `insufficient_evidence`/`partial` + errors，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录 errors/degradations，继续处理其余材料，result.json 仍写合法。
- 缺 output_contract：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态、文献元数据。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据（PII、未公开数据）：只标位置与类型，不复述、不扩散，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --findings /app/work/findings.json --input /input --output /app/output --work /app/work
python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
python3 -B scripts/extract.py --input /input --work /app/work
python3 -B scripts/selftest.py    # 干净环境自检，退出码 0 为通过
```

脚本只依赖 Python 标准库，路径全部用相对路径；在 Skill 包根目录执行。

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境跑一遍 `selftest.py`。示例输出仅作示意，不冒充正式案例结果。
