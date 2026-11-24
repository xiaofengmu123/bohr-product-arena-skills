---
name: source-bound-compliance-audit
description: 对论文或科研材料做投稿前/结题前的学术规范与合规审查（学术诚信、引用、隐私、版权、伦理、投稿要求六方向，外加质量、事实、方法、统计、逻辑），以确定性证据账本驱动逐条判定；每条结论必须给出原文定位、逐字引文、判定依据、严重度与可执行修改建议，并产出可复核的 result.json 与可编辑报告。当需要审查科研材料的学术诚信、引用规范、隐私、版权、伦理与投稿合规时使用。
version: 1.0.0
---

# 学术规范与合规检查 Skill（来源可回查版）

## 1. 角色与何时使用

本 Skill 让 Agent 对科研材料做**逐条、来源可回查、可复跑**的合规审查。与"泛泛提醒"式审查的区别在于：每条结论强制携带四要素——原文位置（file + locator + 逐字 quote）、判定依据（条款/指南/授权范围及其版本与日期）、严重度、可执行修改建议；且脚本产出的候选观察必须逐条裁决为"成立 / 排除 / 待复核"，排除项留档，从而同时抑制漏报与误报。

适用：投稿前、结题前、答辩或对外发布前的质量与合规把关；对象为论文正文、方法/统计段落、参考文献表、图注、数据与代码可用性声明等。

不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会的正式意见。

## 2. 输入契约

- 运行时固定输入：`/input/case.json`（案例元信息、材料索引、可能的 `output_contract` 与 `review_facets`）、`/input/task.md`（题面，可能声明具体审查面）、`/input/` 下材料（PDF、md、txt 等）。
- **必须读实际文件，不依据文件名或标签猜内容。** PDF 先用 `pdftotext`（或 `pdf-text-tool`）解析到 `/app/work/parsed/<原名>.txt` 再建索引；无文本层则如实记录，不猜内容。
- 工作目录 `/app/work/`；最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。
- `case.json` 若声明具体审查面（`review_facets`/`facets`/`checklist`），以其为权威清单；`task.md` 中的补充要求同样要读。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取 `case.json` 的 `case_id`/`id`；缺失用文件名并记录）。
- `output_contract` 的**全部字段**（从 `/input/case.json` 读取；缺值给 `null`/`[]`，不改名、不删键），并把叶子字段平铺到顶层（不覆盖核心键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实反映**审查交付完整度**（`ok`=全部检查面已查且引文回查通过；`partial`=部分覆盖；`insufficient_evidence`=材料不足以审查；`input_error`=输入异常）。
- `report`：`summary`、`scope`、`findings[]`、`dismissed_observations[]`、`checked_items[]`、`pending_review[]`、`not_checked[]`。
- 每条 finding 含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`，外加 `evidence_status`（引文回查结果）与 `quote_locator`。
- `sources`、`uncertainty`、`change_log`、`errors`、`degradations`、`pending_human`、`runtime`（数组或对象）。

同时产出 `/app/output/report.md`（可编辑报告）；`runtime.report_path` 记录其路径。示意输出仅作示例，不冒充正式案例结果。

## 4. 执行流程（总预算 900 秒、64 次模型请求、16384 tokens/响应）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | `case.json` + `task.md` → 记下 case_id、output_contract、声明审查面、材料清单 |
| 1 解析与骨架落盘 | 0–3 min | `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work` → 立即写合法 `result.json`（`status=partial`）+ `report.md` 骨架 + `evidence.json` + `plan.json`。**此后任何中断都已有合法产物** |
| 2 逐条裁决 | 3–20 min | 读 `evidence.json` 候选 + `plan.json` 覆盖表 + 原文 → 每个观察判定成立/排除/待复核，按检查面补查遗漏；每条成立项写全四要素 |
| 3 增量更新 | 随步骤 2 | 把裁决写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings /app/work/findings.json ...` 更新 result.json 与 report.md；每次改动追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` → 修 FAIL；确认契约字段齐备；预算不足时如实 `partial`，不编造 |

**前 3 分钟必须先落盘一版可解析的 result.json 骨架；之后只做增量改进，任何时刻 result.json 都保持合法 JSON。** `findings.json` 为对象：`status/summary/checked_items/not_checked/pending_human/uncertainty/findings[]/dismissed[]/pending[]`，其中 findings 每项可带 `verdict`（`finding|dismissed|pending`）。

## 5. 判定规则（详见 references/facets.md）

- 类型：`fact_error`（可核对事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范/合规）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 检查面：六方向（学术诚信、引用、隐私、版权、伦理、投稿要求）+ 通用质量/事实/方法/统计/逻辑，共 11 个 facet。
- 四要素：原文位置（file+locator+quote）、判定依据（条款/指南/授权原文，写版本与日期）、严重度、可执行修改建议。
- 误报抑制：风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。排除的观察必须写入 `dismissed_observations` 留档。
- 外部文献存在性、图片像素级造假等：仅当运行环境提供检索/渲染工具且已实际核验时才下结论，否则标 `pending_human`。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空
- [ ] `status` 诚实且 ∈ 允许集合；`ok` 时所有检查面已查且 summary 非空
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键），并平铺到顶层
- [ ] `sources`/`uncertainty`/`change_log`/`errors`/`degradations`/`pending_human` 均为数组
- [ ] 每条 finding 含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`
- [ ] 每条 `quote` 能回查原文（`validate.py` 无 `[FAIL]`）
- [ ] 类型四分类与严重度分级正确；误报已排除或降级，排除项留档
- [ ] 11 个 facet 逐项有"已查/待复核/未查"结论，未只挑最容易的一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`/`pending_human`
- [ ] `report.md` 存在可读，`result.json` 记录其路径
- [ ] `change_log` 记录每次增量改动
- [ ] 同一输入重跑结论基本一致（脚本确定性、规则固定）

## 7. 降级策略（详见 references/limitations.md）

- 缺 PDF 文本：依次 pdftotext / pdf-text-tool / OCR；仍不可得 → `insufficient_evidence`/`partial` + `errors` 记录，只覆盖已解析文本。
- 坏 JSON/坏编码：记录到 `errors`/`degradations`，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=input_error`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/main.py report --output /app/output
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/extract.py --root /input --work /app/work
```

## 9. 最小调用示例（示意，非正式案例）

```text
输入：/input/case.json、/input/task.md、/input/paper.pdf
1. run   → /app/output/result.json（status=partial 骨架）+ report.md + /app/work/evidence.json + plan.json
2. 读 evidence/plan/原文 → 裁决写入 /app/work/findings.json
3. merge → 更新 result.json 与 report.md，status 视覆盖度置 ok/partial
4. validate → 无 [FAIL] 即交付
```

## 10. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事；不追求大而全。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境（不同机器/Python 版本）跑一遍自检。
