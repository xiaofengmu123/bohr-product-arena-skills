---
name: academic-integrity-audit
description: 对论文或科研材料做投稿前、结题前或发布前的学术规范与合规审查。以每个案例自带的审查维度（facets）与 output_contract 为范围，逐条输出可回查原文的定位与逐字证据、判定依据、严重度和可执行修改建议，区分事实错误、证据不足、规范问题与建议性意见，显式抑制误报，产出可复核的 result.json 与可编辑的 report.md。当需要审查科研材料的学术诚信、引用、隐私、版权、伦理、投稿要求或方法/统计/逻辑质量时使用。
version: 2.0.0
---

# 学术规范与合规检查 Skill（v2：案例维度驱动 + 全文溯源）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份科研材料做**逐条、可复核**的合规审查。每条问题必须同时给出：原文位置（file+locator）、逐字证据（quote）、判定依据（basis：条款/期刊要求/数据授权范围）、严重度、可执行修改建议。产出不是泛泛提醒，而是能让他人在同一份材料上复跑出基本一致结论的报告。

适用：投稿前、结题前、答辩/发布前的质量与合规把关；以论文正文、补充材料、图表说明、代码、数据说明等真实文件为输入。

不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会的正式意见。

## 2. 核心方法（本版升级点）

1. **案例维度驱动（facets-driven）**：审查范围不以固定六方向为准，而是从 `/input/case.json` 的 `output_contract`、`review_facets`、`facets`、`checklist`、`evidence_requirements`、`finding_types` 等字段读出的**本案例实际检查维度**为准；只有当 case.json 未给出维度时才回退到六方向默认清单（学术诚信、引用、隐私、版权、伦理、投稿要求）。
2. **全文溯源（source-bound）**：每条 quote 必须来自实际解析出的文本，`scripts/validate.py` 会逐条回查原文，编造的 quote 无法通过。
3. **类型化发现（typed findings）**：每条 finding 含 `status ∈ {confirmed, excluded, pending}`；脚本产出的只是候选观察（observation），必须经 Agent 判定成立/排除/待复核后写入 findings。
4. **误报抑制**：风格差异、学科惯例、个人表述、非母语措辞不判违规；冲突信息同时记录并标 `source_conflict`；拿不准标 `pending_human`。
5. **确定性**：脚本无随机、固定排序与布局，同一输入两次运行结论一致。

## 3. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、`output_contract`、可能的审查维度）、`/input/task.md`（题面）、`/input/` 下材料（PDF、md、txt、json、csv 等）。
- PDF 原文必须自行解析：优先用 `pdftotext`（脚本自动调用），失败再用平台 `pdf-text-tool`/OCR；**必须读实际文件，不依据文件名或标签猜内容**。
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码降级（gb18030/latin-1）、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。

## 4. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取自 case.json 的 `case_id`/`id`；缺失时用文件名并记录）。
- `output_contract`：从 case.json 读出的**全部字段原样回填**，缺值给 `null`/`[]`，不改名、不删未知键；同时把叶子字段平铺到顶层（不覆盖 `case_id/status/report/sources/uncertainty/change_log/errors/degradations/pending_human/runtime` 等核心键）。无 contract 时写 `{}` 并标 `contract_missing=true`。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，按实填写（见第 8 节）。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision/status`；`type ∈ {fact_error, insufficient_evidence, norm_violation, suggestion}`，`severity ∈ {critical, major, minor, info}`。
- `report.checked_items[]` / `pending_review[]` / `not_checked[]`：覆盖本案例每个审查维度，未只挑最容易的一条。
- `sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`（均数组/对象）。

同时产出 `/app/output/report.md`（可编辑审查报告，result.json 记录其路径）。result.json 必须是合法 JSON，任何时刻可解析。

## 5. 执行流程（总预算 900 秒、64 次模型请求，每次 16384 token）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | 读 `/input/case.json` + `/input/task.md` → 记下 case_id、output_contract、**本案例审查维度**、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | 运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` → 立即写合法 result.json 骨架（status=partial）+ `evidence.json` + `plan.md`。**此后任何中断都有合法产物** |
| 2 逐条审查 | 3–18 min | 读 `evidence.json` 的候选观察与全文段落 → 逐条判定成立/排除/调严重度，按案例维度补查遗漏；每条写全字段，quote 必须逐字回查 |
| 3 增量更新 | 随步骤 2 | findings 写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings /app/work/findings.json ...` 更新 result.json 并重写 report.md；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` → 修 FAIL；确认契约字段齐备；预算不足如实 partial，不编造 |

前 3 分钟必须先落盘一版可解析 result.json；之后只做**增量改进**。

## 6. 判定规则（详见 references/audit-rules.md）

- 类型四分类：`fact_error`（事实错误）、`insufficient_evidence`（证据不足/方法统计交代不清）、`norm_violation`（规范/合规问题）、`suggestion`（改进建议）。
- 严重度四级：`critical`（可能被撤稿/伦理或授权硬伤）、`major`（影响结论可信度或投稿）、`minor`（格式/局部问题）、`info`（提示）。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis，写条款/期刊要求/授权范围原文，无法给条款时明确标 `basis_unavailable`）、严重度、可执行修改建议。
- 误报抑制：脚本产出的是候选观察，必须逐条判定；风格差异、学科惯例、个人表述不判违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。

## 7. 自检清单（对应评分维度）

- [ ] result.json 可解析，`case_id` 非空（实际效果-可核验性）
- [ ] `status` 诚实且 ∈ 允许集合（可靠体验-运行稳定性）
- [ ] `output_contract` 全部字段存在，缺值 null/[]，未改名未删键（实际效果-完整性）
- [ ] `sources`/`uncertainty`/`change_log` 均非空（可靠体验-异常处理）
- [ ] 每条 finding 含全部必需字段，`quote` 经 validate.py 回查原文通过（实际效果-正确性/可核验性）
- [ ] 类型四分类与严重度分级正确，误报已排除或降级（实际效果-正确性）
- [ ] 本案例每个审查维度均有"已查/待复核/未查"结论，未只挑最容易的一条（实际效果-完整性）
- [ ] 材料缺失/解析失败/软件缺失已写入 errors/degradations（可靠体验-异常处理）
- [ ] report.md 存在可读，result.json 记录其路径（生态贡献-文档完整性）
- [ ] change_log 记录每次增量改动（可靠体验-运行稳定性）
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）（可靠体验-运行稳定性）

## 8. 降级策略（详见 references/limitations.md）

- 缺 PDF 文本：依次 pdftotext → 平台 pdf-text-tool → OCR；仍不可得 → `insufficient_evidence`/`partial` + errors 记录，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 9. 脚本调用（相对路径，标准库 only）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
```

## 10. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境跑一遍自检。
