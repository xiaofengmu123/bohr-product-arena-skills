---
name: source-bound-audit
description: 对论文与科研材料做来源锚定的学术规范审查（学术诚信、引用、隐私、版权、伦理、投稿要求，外加质量/方法/统计/逻辑），每条问题必须指回原文具体位置、给出逐字证据、判定依据、严重度与可执行修改建议；逐检查面给出 ok/issues/pending/not_checked 结论，产出可复核的 result.json 与可编辑 report.md。当需要审查科研材料的合规性、引用真实性、方法统计严谨性与投稿就绪度时使用。
version: 1.0.0
---

# 来源锚定的科研材料规范审查 Skill（Source-bound Audit）

## 1. 角色与何时使用

本 Skill 让 Agent 对一份科研材料做**逐条、可复核、来源锚定**的合规审查：报出的每个问题都必须指回原文具体位置并附逐字证据，绝不允许只给"建议检查参考文献格式"这类泛泛提醒。核心方法论是"脚本提出候选观察，Agent 核对原文后判定"——脚本的 pattern 命中只是候选，不是结论，从而在漏报与误报之间取平衡。

适用：投稿前、结题前、答辩/发布前的质量与合规把关。不适用：润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 三条硬约束

1. **来源锚定**：每个 finding 的 `quote` 必须能从原文逐字回查（`validate.py` 校验）；查不到原文证据的观察不得升级为 finding。
2. **无强制判定**：材料干净就报干净、缺证据就报不足，不预设"必须找出问题"；对材料不涉及的面标 `ok` 并注明"不涉及"。
3. **先落盘再增量**：前 3 分钟必须写出一版合法 `result.json` 骨架；此后只做增量改进，任何时刻产物都是合法 JSON。

## 3. 输入契约

- 固定输入：`/input/case.json`（案例元信息、可能的 `output_contract`）、`/input/task.md`（题面）、`/input/` 下的材料（PDF、md、txt 等）。
- PDF 原文须自行解析：优先 `pdftotext`（预装），失败则用平台 `pdf-text-tool`，仍不可得用 `ocr-tool`；**必须读实际文件，不依据文件名/标签猜内容**。
- 工作目录 `/app/work/`；最终产物与可编辑文件写 `/app/output/`。
- 容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。

## 4. 输出契约

必须产出 `/app/output/result.json` 与 `/app/output/report.md`。`result.json` 至少含：

- `case_id`（取自 `case.json` 的 `case_id`/`id`；缺失用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，不改名、不删键），并把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.summary`、`report.findings[]`（每条含 `id/type/severity/category/facet/title/file/locator/quote/evidence/basis/basis_rule/revision`）、`report.facets[]`（十面逐项结论）。
- `sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime` 均为数组/对象且非空。

`report.md` 由脚本从 `result.json` 生成，保持与 JSON 一致；Agent 可直接编辑 JSON 后重新 merge 再生成。

## 5. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | `/input/case.json` + `task.md` → 记下 case_id、output_contract、检查范围、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | 运行 `python3 -B scripts/main.py run ...` → 写 `result.json` 骨架（status=partial）+ `evidence.json` + `plan.md`。**此后任何中断都已有合法产物** |
| 2 逐检查面审查 | 3–20 min | 读 `evidence.json` 候选 + 原文 → 对十个检查面逐项判定 `ok/issues/pending/not_checked`，每条 finding 写全证据四要素 |
| 3 增量更新 | 随步骤 2 | findings 写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge ...` 更新 result.json；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py ...` → 修 FAIL；再 `python3 -B scripts/main.py finalize ...` 定 status；预算不足如实 partial，不编造 |

## 6. 判定规则（摘要，详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范/合规问题）、`suggestion`（建议）。
- 严重度：`critical/major/minor/info`；先按影响定级，再复核是否降/升一级。
- 十个检查面：学术诚信、引用规范、隐私、版权、伦理、投稿要求、质量、方法、统计、逻辑。
- 证据四要素：原文位置（file+locator+quote）、判定依据（basis，写条款/指南/授权范围原文及版本）、严重度、可执行修改建议（revision）。
- 误报抑制：风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。

## 7. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空
- [ ] `status` 诚实且 ∈ 允许集合
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键）
- [ ] `sources`/`uncertainty`/`change_log` 均为非空数组
- [ ] 每条 finding 含 `id/type/severity/category/facet/title/file/locator/quote/evidence/basis/basis_rule/revision`
- [ ] 每条 `quote` 能逐字回查原文（validate.py 校验通过）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级
- [ ] 十个检查面逐项有"已查/待复核/未查"结论，未只挑一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`
- [ ] `report.md` 存在可读，与 result.json 一致
- [ ] `change_log` 记录每次增量改动
- [ ] 同一输入重跑结论基本一致（脚本确定性）

## 8. 降级策略（详见 references/checklist.md）

- 缺 PDF 文本：pdftotext → pdf-text-tool → OCR；仍不可得 → `partial`/`insufficient_evidence` + errors，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 9. 脚本调用（相对路径，标准库 only）

```bash
# 在 Skill 包根目录（SKILL.md 所在目录）执行；python3 不存在时用 python
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/extract.py --root /input --work /app/work
```

## 10. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事，不追求大而全。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境跑一遍自检。
