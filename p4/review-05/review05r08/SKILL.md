---
name: source-bound-academic-audit
description: 对论文或科研材料做投稿/结题/答辩前的逐条、可复核学术规范与合规审查（学术诚信、引用、隐私、版权、伦理、投稿要求，外加质量/方法/统计/逻辑）。每条结论必须绑定原文定位与可核对依据，区分事实错误、证据不足、规范问题与建议，产出合法 result.json、可编辑 report.md 与证据索引；当需要审查科研材料的学术规范、引用真实性、数据合规与投稿要求时使用。
version: 2.0.0
---

# 学术规范与合规检查 Skill（source-bound 版）

## 1. 角色与何时使用

让 Agent 对科研材料做**逐条、可复核**的合规审查：每条问题指回原文位置（file + locator）、给逐字 quote、判定依据（basis）、严重度与可执行修改建议，而不是泛泛提醒。适合投稿前、结题前、答辩/发布前把关。

不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

本版相对常见做法的新增价值：**每个结论都强制绑定原文片段与依据来源（source-bound）**；用确定性证据索引支撑六方向全量检查，抑制误报；用 `basis_kind` 区分"材料内证/外部条款/元数据/待复核/未报告"，禁止编造条款、期刊要求、DOI 与数值。

## 2. 输入契约

- 固定输入：`/input/case.json`（case_id、`output_contract`、可能的 `review_facets`/`categories`/`finding_types`）、`/input/task.md`（题面）、`/input/` 下材料（PDF、md、txt 等）。
- PDF 原文须自行解析：用平台 `pdf-text-tool`（或 `pdftotext`）把每份 PDF 解析到 `/app/work/parsed/<原名>.txt`；**必须读实际文件，不依据标签猜内容**。
- 工作目录 `/app/work/`；最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃；单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取 `case.json` 的 `case_id`/`id`；缺失时用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，不改名、不删键），同时把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/basis_kind/revision`（`basis_kind` 见 references/audit-rules.md）。
- `sources`、`uncertainty`、`change_log`（数组，每次增量改动都追加）、`errors`、`degradations`、`pending_human`、`runtime`。

同时产出 `/app/output/report.md`（可编辑审查报告，脚本会自动按 finding 重生成，Agent 可再补结论摘要与依据）。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | `/input/case.json` + `task.md` → 记 case_id、output_contract、review_facets、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | PDF → `/app/work/parsed/*.txt`；`python3 -B scripts/main.py --input /input --output /app/output --work /app/work` → 立即写 `result.json` 骨架（status=partial）+ `report.md` + `evidence.json` + `plan.md`。**此后任何中断都已有合法产物** |
| 2 逐条审查 | 3–20 min | 读 `evidence.json` 的 signals 候选 + 原文 → 判定成立/排除/调严重度，按六方向 + 质量/方法/统计/逻辑逐项补查；每条写全 12 个字段 |
| 3 增量更新 | 随步骤 2 | 写 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings /app/work/findings.json ...` 更新 result.json；自动重生成 report.md；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` → 修 FAIL；确认契约字段齐备；预算不足如实 partial，不编造 |

前 3 分钟必须先落盘可解析的 result.json 骨架；之后只做**增量改进**，任何时刻 result.json 都保持合法 JSON。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`、`insufficient_evidence`、`norm_violation`、`suggestion`；严重度 `critical/major/minor/info`。
- 方向：学术诚信、引用规范、隐私与数据合规、版权与许可、伦理审查、投稿要求 + 通用质量/方法/统计/逻辑；若 case.json 提供 `review_facets`/`categories`，以其为准覆盖默认。
- 四要素：位置（file+locator+quote）、依据（basis + basis_kind + 必要时 basis_source）、严重度、可执行修改建议。
- 误报抑制：脚本 signals 只是候选，必须逐条判定；风格差异、学科惯例、个人表述不算违规；冲突同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。
- 依据绑定：`normative_clause` 必须带条款来源（名称/URL/版本/日期）；无来源则降为 `pending_human`。禁止编造条款、期刊要求、文献元数据或 DOI。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 诚实且在允许集合内
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键），叶子已平铺
- [ ] `sources`/`uncertainty`/`change_log` 均为非空数组；`errors`/`degradations` 如实记录
- [ ] 每条 finding 含 12 个必需字段；`quote` 逐字可回查（validate.py 通过）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级（对应"结果正确性"）
- [ ] 六方向 + 通用方向逐项有"已查/待复核/未查"结论，未只挑最容易的一条（对应"结果完整性"）
- [ ] 每条 `basis_kind` 合理；`normative_clause` 带 basis_source（对应"结果可核验性"）
- [ ] `report.md` 存在可读，`result.json` 的 `report` 与其一致（对应"调用与易用性"）
- [ ] `change_log` 记录每次增量改动；`merge` 幂等可重跑（对应"运行稳定性/增益可验证性"）
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/limitations（对应"异常处理"）
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）
- [ ] 说明与脚本版本一致，他人可按 SKILL.md 独立复跑（对应"生态贡献"）

## 7. 降级策略（详见 references/contract-guide.md）

- 缺 PDF 文本：依次 pdf-text-tool / pdftotext / OCR；仍不可得 → `insufficient_evidence`/`partial` + errors 记录，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only，在 Skill 包根目录执行）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work --status ok --summary "..."
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/extract.py --root /input --work /app/work
```

脚本只依赖 Python 标准库，无第三方依赖；`main.py` 与 `extract.py` 同目录相对导入。

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事。无真实材料时输出"材料不足"而非示例填充；不覆盖论文代写、数据重分析、法务/伦理正式意见。交付前在干净环境（不同机器/Python 版本）跑一遍自检。
