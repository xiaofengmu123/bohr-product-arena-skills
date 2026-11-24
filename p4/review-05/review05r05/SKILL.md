---
name: academic-integrity-audit
description: 对论文或科研材料做投稿前/结题前的学术规范与合规审查，按案例自带 review facets 或六方向逐条检查质量、引用、事实、方法、统计、逻辑与规范，每条问题回指原文位置、给出判定依据、严重度与可执行修改建议，并产出可复核的 result.json 与可编辑报告。当需要审查科研材料的学术诚信、引用规范、隐私、版权、伦理与投稿要求时使用。
version: 2.0.0
---

# 学术规范与合规检查 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 对科研材料做**逐条、可复核、源绑定（source-bound）**的合规审查：每条问题必须回指原文具体位置（file + locator + 逐字 quote）、给出判定依据、严重度和可执行修改建议，而不是泛泛提醒。适用于投稿前、结题前、答辩/发布前的质量与合规把关。

不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、材料索引、可能的 `output_contract` 与 `review_facets`）、`/input/task.md`（题面）、`/input/` 下材料（PDF、md、txt 等）。
- 检查方向**以 case.json 自带 facets 为准**：依次读 `review_facets` / `facets` / `audit_facets` / `checklist` / `review_dimensions`（顶层或 `output_contract` 内）；缺失时才用默认六方向 + 通用方向。
- PDF 原文须自行解析：先用平台 `pdf-text-tool`（或 `pdftotext`）把每份 PDF 解析到 `/app/work/parsed/<原名>.txt`，再让脚本建索引；**必须读实际文件，不依据标签猜内容**。
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取自 `case.json` 的 `case_id`/`id`；缺失时用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，不改名、不删键）；同时把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.facets` 与 `report.facet_coverage`：按实际检查方向逐项给状态（`ok/issue/pending`）与问题数。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`，以及 `quote_verified/quote_match_mode`。
- `sources`、`uncertainty`、`change_log`（数组，记录每次增量改动）、`errors`、`degradations`、`pending_human`、`runtime`、`report_path`、`input_fingerprint`。

同时产出 `/app/output/report.md`（可编辑审查报告，由脚本根据 result.json 重建）。示例输出仅作示意，不冒充正式案例结果。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | `/input/case.json` + `task.md` → 记下 case_id、output_contract、review facets、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | PDF → `/app/work/parsed/*.txt`；`python3 -B scripts/main.py run` → 立即写 `result.json` 骨架（status=partial）+ `evidence.json` + `plan.md` + `report.md`。**此后任何中断都已有合法产物** |
| 2 逐条审查 | 3–20 min | 读 `evidence.json` 候选观察 + 原文 → 判定成立/排除/调严重度，按 facets 补查遗漏；每条写全四要素 |
| 3 增量更新 | 随步骤 2 | findings 写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings ...` 更新 result.json；同步重建 report.md；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py` → 修 FAIL；确认契约字段齐备；预算不足时如实 partial，不编造 |

前 3 分钟必须先落盘一版可解析的 result.json 骨架；之后只做**增量改进**，任何时刻 result.json 都保持合法 JSON。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error` / `insufficient_evidence` / `norm_violation` / `suggestion`；严重度 `critical/major/minor/info`。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis，写条款/期刊要求/授权范围原文）、严重度、可执行修改建议。
- 误报抑制：脚本产出的是候选观察（observation），必须逐条判定；风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空
- [ ] `status` 诚实且 ∈ 允许集合
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键），叶子键平铺顶层
- [ ] `sources`/`uncertainty`/`change_log` 均为数组且非空
- [ ] 每条 finding 含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`
- [ ] 每条 `quote` 能回查原文（validate.py 校验通过）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级
- [ ] 各检查方向逐项有"已查/待复核/未查"结论，未只挑最容易的一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/`pending_human`
- [ ] `report.md` 存在可读，`result.json` 记录其路径
- [ ] `change_log` 记录每次增量改动
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）

## 7. 降级策略（详见 references/checklist.md）

- 缺 PDF 文本：依次 pdf-text-tool/pdftotext/OCR；仍不可得 → `insufficient_evidence`/`partial` + errors 记录，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
# 在 Skill 包根目录（SKILL.md 所在目录）执行
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --findings /app/work/findings.json --input /input --output /app/output --work /app/work [--status ok]
python3 -B scripts/main.py finalize --output /app/output [--status ok]
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/validate.py selftest
```

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事；不追求大而全。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境（不同机器/Python 版本）跑一遍 `selftest`。
