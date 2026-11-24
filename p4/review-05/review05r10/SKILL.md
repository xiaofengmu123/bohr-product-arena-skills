---
name: academic-integrity-audit
description: 对论文或科研材料做投稿前/结题前的学术规范与合规检查（学术诚信、引用、隐私、版权、伦理、投稿要求，外加质量、统计、方法、逻辑、规范），采用「证据先行、判定在后、反证复核」的 source-bound 流程：脚本只产出可回查的候选观察与反证提示，Agent 逐条证伪后才形成 finding，每条 finding 都带原文位置、逐字 quote、依据类型（basis_kind）与可执行修改建议，并产出可复核的 result.json 与可编辑 report.md。当需要审查科研材料的学术诚信、引用规范、隐私、版权、伦理与投稿要求，且要求每条结论可指回原文时使用。
version: 3.0.0
---

# 学术规范与合规检查 Skill（source-bound / evidence-first）

## 1. 角色与何时使用

本 Skill 让 Agent 对科研材料做**逐条、可复核**的合规审查：报出的每条问题都必须指回
原文具体位置、给出判定依据、严重度和可执行修改建议，而不是泛泛提醒。核心理念是
**证据先行、判定在后、反证复核**——机器只给候选与反证，Agent 逐条证伪后才落 finding。

适用于投稿前、结题前、答辩/发布前的质量与合规把关。不适用：论文润色/代写/代投稿、
数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、材料索引、可能的 `output_contract`、
  可能的 `review_facets/facets/finding_types/categories` 等审查面声明）、`/input/task.md`
  （题面）、`/input/` 下材料（PDF、md、txt 等）。
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- **必须读实际文件，不依据标签猜内容**。PDF 须自行解析：脚本先尝试 `pdftotext`，不可得
  时用平台 `pdf-text-tool`（或 `pdftotext`/OCR）解析到 `/app/work/parsed/<原名>.txt`；
  `run` 会自动索引已存在于 `work/parsed/` 的文本层（解析后重跑一次 `run` 即可）。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。
- 若 `case.json` 声明了审查面/类型/严重度，以声明为准覆盖内置默认（见 `scripts/extract.py`）。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取自 `case.json` 的 `case_id`/`id`；缺失时用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，
  不改名、不删键）；同时把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，语义见
  `references/audit-rules.md` 第 6 节。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/
  evidence/basis/revision`，并建议加 `basis_kind/basis_source/counter_check`。
- `sources`、`uncertainty`、`change_log`（每次增量改动）、`errors`、`degradations`、
  `pending_human`、`runtime`。

同时产出 `/app/output/report.md`（可编辑审查报告）。示例输出仅作示意，不冒充正式案例结果。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | 读 `/input/case.json` + `task.md` → 记下 case_id、output_contract、审查面清单、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | PDF → `/app/work/parsed/*.txt`；运行 `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work` → 立即写 `result.json` 骨架（status=partial）+ `evidence.json` + `plan.md`。**此后任何中断都已有合法产物** |
| 2 逐条审查与证伪 | 3–20 min | 读 `evidence.json` 候选观察 + 反证提示 + 原文 → 对每条候选判 确认/排除/降级/待复核；按全部审查面补查遗漏；每条 finding 写全字段 + `basis_kind` |
| 3 增量更新 | 随步骤 2 | 把 findings/coverage 写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge ... --findings /app/work/findings.json` 更新 result.json；同步写 report.md；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` → 修 FAIL；确认契约字段齐备；预算不足时如实 partial，不编造 |

**前 3 分钟必须先落盘一版可解析的 result.json 骨架**；之后只做**增量改进**，任何时刻
result.json 都保持合法 JSON。`merge` 幂等：findings 内容未变时不会重复追加 change_log。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、
  `norm_violation`（规范/合规问题）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis + basis_kind +
  basis_source）、严重度、可执行修改建议。
- 依据绑定：规范条款/期刊指南必须写条款编号/版本/日期，未核对的标 `unverified` 或转
  `pending_human`；不得编造 DOI、期刊要求、文献元数据。
- 误报抑制：脚本产出的是候选观察（observation），必须逐条证伪；风格差异、学科惯例、
  个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`。
- 反证复核：涉及复用/隐私/伦理的候选，先查 `counter_hints`（已授权/许可/脱敏/豁免），
  找到反证则排除或降级（`counter_check.result = "contradicted"`）。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空
- [ ] `status` 诚实且 ∈ 允许集合（语义见 audit-rules.md）
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键）
- [ ] `sources`/`uncertainty`/`change_log` 均为数组且非空
- [ ] 每条 finding 含 `id/type/severity/category/file/locator/quote/evidence/basis/revision`
- [ ] 每条 finding 含 `basis_kind`（依据类型），规范类还含 `basis_source`
- [ ] 每条 `quote` 能回查原文（validate.py 校验通过，写入 quote_match_mode/ratio）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级（counter_check 留档）
- [ ] 全部审查面逐项有"已查/待复核/未查"结论，未只挑最容易的一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/limitations
- [ ] `report.md` 存在可读，`result.json` 记录其路径
- [ ] `change_log` 记录每次增量改动；`merge` 幂等（同内容不重复记日志）
- [ ] 同一输入重跑结论基本一致（input_fingerprint + 确定性排序）

## 7. 降级策略（详见 references/facets.md 与 audit-rules.md）

- 缺 PDF 文本：依次用 `pdftotext` / 平台 `pdf-text-tool` / OCR；仍不可得 →
  `insufficient_evidence`/`partial` + errors 记录，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）与最小调用示例

```bash
# 在 Skill 包根目录（SKILL.md 所在目录）执行
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/validate.py selftest
```

最小调用示例（示意，非正式案例结果）：某案例 `/input` 含 `case.json`（`case_id` 与
`output_contract`）与一篇 `paper.md`。执行 `run` 后 `/app/output/result.json` 的骨架为
`{"case_id":"...", "status":"partial", "output_contract":{...}, "sources":[...],
"report":{"findings":[],"coverage":{...}}}`；Agent 判读后写 `findings.json` 再 `merge`，
`status` 变为 `partial` 或 `ok`。示意输出仅供说明接口，不冒充正式案例结果。

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事；不追求大而全。无真实材料时输出
"材料不足"而非示例填充；交付前在干净环境（不同机器/Python 版本）跑一遍
`validate.py selftest`。
