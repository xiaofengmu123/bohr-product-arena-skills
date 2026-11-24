---
name: source-bound-audit
description: 对论文或科研材料做投稿前/结题前的学术规范与合规审查，采用"证据先行、判定分离"两阶段流程：确定性脚本抽取可复核的候选观察（每条带 locator 与逐字 quote），Agent 逐条判定确认/排除/改级并记录理由，产出含 evidence_map、拒绝日志、六方向覆盖矩阵的 result.json 与可编辑 report.md。当需要逐条、可复核地审查科研材料的学术诚信、引用、隐私、版权、伦理与投稿要求时使用。
version: 1.0.0
---

# 证据溯源式学术合规审查 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 对科研材料做**逐条、可复核、可复跑**的合规审查。核心不是"列检查项"，而是三件事：报出的每条问题都能指回原文具体位置并附逐字片段；每条判定都有可核对的依据（规范条款、期刊要求、授权范围原文）；换一个 Agent 在同一材料上能得出基本一致的结论。

适用：投稿前、结题前、答辩/发布前的学术规范把关。不适用：论文润色/代写/代投稿、数据重分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 固定输入：`/input/case.json`（案例元信息、材料索引、`output_contract`）、`/input/task.md`（题面）、`/input/` 下的材料（PDF、md、txt 等）。
- 必须先读实际文件，不依据标签猜内容；PDF 用 `pdftotext`（备选平台 `pdf-text-tool`）解析到 `/app/work/parsed/<原名>.txt` 再建索引。
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码逐级降级、坏 JSON 记录不崩溃；单文件 >2MB 跳过并记入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取 `case.json` 的 `case_id`/`id`，缺失用文件名并记录）。
- `output_contract` 的**全部字段**：从 `/input/case.json` 原样回填，缺值给 `null`/`[]`，不改名、不删键；叶子字段同时平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/evidence/basis/revision`。
- `evidence_map`：`finding_id → anchors[]`，每个锚点含 `file/locator/quote/role`（primary/supporting/contradicting）。
- `report.rejections[]`：每条含 `observation_id/reason`，记录被排除的候选观察与排除理由。
- `report.coverage`：十方向（六方向 + 质量/方法/统计/逻辑）逐项标 `checked/pending/not_checked`。
- `sources`、`uncertainty`、`change_log`（记录每次增量）、`errors`、`degradations`、`pending_human`、`runtime`。

同时产出 `/app/output/report.md`（可编辑报告，可由 `main.py report` 从 result.json 重建）。示例输出仅作示意，不冒充正式案例结果。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 确认契约 | 0–1 min | `/input/case.json` + `task.md` → 记下 case_id、output_contract、检查范围、材料清单 |
| 1 抽取与骨架落盘 | 0–3 min | 运行 `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` → 立即写 `result.json` 骨架（status=partial）+ `evidence.json` + `plan.md`。**此后任何中断都有合法产物** |
| 2 逐条判定 | 3–20 min | 读 `evidence.json` 候选观察 + 原文 → 判定确认/排除/改类型或严重度，按十方向补查遗漏；每条写全 11 字段 |
| 3 增量更新 | 随步骤 2 | 把判定写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge --findings /app/work/findings.json ...` 更新 result.json；每次追加 change_log |
| 4 自检收敛 | 最后 2–3 min | `python3 -B scripts/validate.py --input /input --output /app/output --work /app/work` → 修 FAIL；预算不足时如实 partial，不编造 |

前 3 分钟必须先落盘一版可解析的 result.json 骨架；之后只做**增量改进**，任何时刻 result.json 都保持合法 JSON。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范/合规）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 十方向：学术诚信、引用规范、隐私与数据合规、版权与许可、伦理审查、投稿要求 + 质量/方法/统计/逻辑。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis，写条款/期刊要求/授权范围原文）、严重度、可执行修改建议。
- 误报抑制：脚本产出的是候选观察（observation），必须逐条判定；风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] `result.json` 可解析，`case_id` 非空（结果可核验性）
- [ ] `status` 诚实且 ∈ 允许集合（运行稳定性）
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键）
- [ ] `sources`/`uncertainty`/`change_log` 均为数组且非空（完整性）
- [ ] 每条 finding 含 11 个必需字段（任务完成度）
- [ ] 每条 `quote` 能回查原文（validate.py 校验通过，正确性）
- [ ] `rejections` 记录每个被排除观察及理由（误报抑制）
- [ ] 类型四分类与严重度分级正确；依据引对、不编造条款（正确性）
- [ ] 十方向逐项有"已查/待复核/未查"结论，未只挑最容易的一条（完整性）
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`（异常处理）
- [ ] `report.md` 存在可读，result.json 记录其路径（易用性）
- [ ] `change_log` 记录每次增量改动（可复核性）
- [ ] 同一输入重跑结论一致（`validate.py --selftest` 验证确定性）

## 7. 降级策略（详见 references/audit-rules.md）

- 缺 PDF 文本：依次 pdftotext → pdf-text-tool → 记 `insufficient_evidence`/`partial` + errors，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

```bash
# 在 Skill 包根目录（SKILL.md 所在目录）执行
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
python3 -B scripts/validate.py --selftest
python3 -B scripts/extract.py --root /input --work /app/work
```

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事；不追求大而全。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境跑一遍 `--selftest`。
