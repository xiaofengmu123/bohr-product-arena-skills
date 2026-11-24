---
name: source-bound-compliance-audit
description: 对论文或科研材料做投稿前/结题前的学术规范与合规检查（学术诚信、引用、隐私、版权、伦理、投稿要求，以及质量/方法/统计/逻辑），逐条输出原文位置、判定依据、严重度与可执行修改建议，并产出可复核的 result.json 与可编辑报告。当需要审查科研材料的学术诚信、引用规范、隐私、版权、伦理与投稿要求时使用。
version: 1.1.0
---

# 学术规范与合规检查 Skill（source-bound compliance audit）

## 1. 角色与何时使用

本 Skill 让一个 Agent 对科研材料做**逐条、可复核**的合规审查：报出的每条问题必须指回原文具体位置、给出判定依据、严重度和可执行修改建议，而不是泛泛提醒。核心设计是"**候选 → 证伪 → 结论**"：脚本只产出候选观察，Agent 逐条证伪后才提升为 finding，从而压制误报。

适用于投稿前、结题前、答辩/发布前的质量与合规把关。不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、材料索引、可能的 `output_contract`）、`/input/task.md`（题面）、`/input/` 下材料（PDF、md、txt 等）。
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- **必须读实际文件**，不依据标签或文件名猜内容；PDF 用平台 `pdf-text-tool`/`pdftotext` 解析原文后再审查。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 不崩溃、单文件 >2MB 跳过并记录（见 `references/limitations.md`）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取自 `case.json` 的 `case_id`/`id`；缺失时用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，不改名、不删键）；同时把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`，并加 `verdict ∈ {confirmed, suspected, excluded, pending_human}` 与 `evidence_tier ∈ {verbatim, inference, external}`。
- `sources`、`uncertainty`、`change_log`（每次增量改动的数组）、`errors`、`degradations`、`pending_human`、`runtime`。

同时产出 `/app/output/report.md`（可编辑审查报告，result.json 与其内容一致）。类型四分类、严重度四级、证据层级、误报抑制与六方向逐项规则见 `references/audit-rules.md`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | 读 `/input/case.json` + `task.md` → 记下 case_id、output_contract、检查范围、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | 运行 `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work` → 立即写 `result.json` 骨架（status=partial）+ `report.md` + `observations.json` + `coverage.json` + `plan.md`。**此后任何中断都已有合法产物** |
| 2 逐条证伪审查 | 3–18 min | 读 `observations.json` 候选 + 原文 → 判定 confirmed / excluded / suspected / pending_human，按六方向补查遗漏；每条写全字段 |
| 3 增量更新 | 随步骤 2 | 把 findings + coverage + change_log 写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings /app/work/findings.json --input /input --output /app/output --work /app/work` |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work` → 修 FAIL；确认契约字段齐备；预算不足时如实 partial，不编造 |

前 3 分钟必须先落盘一版可解析的 result.json 骨架；之后只做**增量改进**，任何时刻 result.json 都保持合法 JSON。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范问题）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 六方向：学术诚信、引用规范、隐私与数据合规、版权与许可、伦理审查、投稿要求，外加通用质量/方法/统计/逻辑。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis，写条款/指南/授权范围原文与版本日期）、严重度、可执行修改建议。
- 误报抑制：脚本产出的是候选观察（observation），必须逐条证伪；风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空（任务完成度/可核验性）
- [ ] `status` 诚实且 ∈ 允许集合（结果正确性）
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键），叶子字段已平铺顶层（结果完整性）
- [ ] `sources`/`uncertainty`/`change_log` 均为数组且非空（可核验性）
- [ ] 每条 finding 含 11 个必填字段 + `verdict` + `evidence_tier`（任务完成度）
- [ ] 每条 `quote` 能回查原文（`main.py validate` 通过）（结果可核验性）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级（结果正确性）
- [ ] 六方向逐项有 checked/pending_review/not_checked 结论，未只挑最容易的一条（结果完整性）
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/limitations（异常处理）
- [ ] `report.md` 存在可读，与 result.json 一致（易用性）
- [ ] `change_log` 记录每次增量改动（可复核）
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）（运行稳定性）

## 7. 降级策略（详见 references/limitations.md）

- 缺 PDF 文本：依次用 pdftotext / 平台 pdf-text-tool / OCR；仍不可得 → `insufficient_evidence`/`partial` + errors，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --findings /app/work/findings.json --input /input --output /app/output --work /app/work
python3 -B scripts/main.py validate --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selftest
```

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境（不同机器/Python 版本）跑一遍 `selftest`。
