---
name: evidence-anchored-academic-audit
description: 对科研材料做逐条、可复核、防误报的学术规范与合规审查：确定性抽取原文与候选观察，逐条由 Agent 判定（成立/排除/降级），每条问题绑定原文位置、判定依据（禁止编造条款）、严重度与可执行修改建议，输出十项覆盖矩阵、可复核的 result.json 与可编辑报告。当需要审查论文或科研材料的学术诚信、引用规范、隐私、版权、伦理、投稿要求及质量/统计/方法/逻辑时使用。
version: 1.0.0
---

# 证据锚定式学术合规审查 Skill

## 1. 角色与何时使用

本 Skill 让 Agent 对科研材料做**逐条、可复核、防误报**的合规审查。核心不是"列多少检查项"，而是每条问题都必须：指回原文位置（file + locator + 逐字 quote）、给出绑定到权威来源的判定依据（basis）、给出严重度与可执行修改建议。产出可被他人复跑的 `result.json` 与可编辑报告。

不适用：论文润色/代写/代投稿、数据重新分析、实验方案设计；不提供法务或伦理委员会正式意见。

## 2. 输入契约

- 固定输入：`/input/case.json`（案例元信息、`output_contract`）、`/input/task.md`（题面）、`/input/` 下材料（PDF、md、txt、csv 等）。
- PDF 原文必须实际解析：脚本先复用 `/app/work/parsed/<原名>.txt`，没有则调用系统 `pdftotext`；仍失败就用平台 `pdf-text-tool` / `ocr-tool` 提取后放到上述路径，再重跑。**必须读实际文件，不依据标签或领域提示猜内容。**
- 工作目录 `/app/work/`，最终产物与可编辑文件写 `/app/output/`。
- 读取容错：`utf-8-sig` 优先、坏编码降级、坏 JSON 记录不崩溃、单文件 >2MB 跳过并记录。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少包含：

- `case_id`（取自 case.json 的 `case_id`/`id`/`challenge_id`/`task_id`；缺失时用文件名并记录）。
- `output_contract`：从 `/input/case.json` **原样回填全部字段**（缺值给 `null`/`[]`，不改名、不删未知键）；同时把叶子字段平铺到顶层（不覆盖核心审计键）。
- `status ∈ {ok, partial, insufficient_evidence, input_error}`，如实填写。
- `report.findings[]`：每条含 `id/type/severity/category/title/file/locator/quote/candidate/verdict/adjudication/evidence/basis/revision/confidence/pending`。
- `report.coverage[]`：十个检查面逐项给 `checked_clean/finding/insufficient_evidence/not_applicable/not_checked`，证明覆盖面而非只挑容易的一条。
- `sources`、`uncertainty`、`change_log`（记录每次增量改动）、`errors`、`degradations`、`pending_human`、`runtime`、`verification`。

同时产出 `/app/output/report.md`（可编辑审查报告）。示例输出仅作示意，不冒充正式案例结果。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步骤 | 时间 | 输入 → 输出 |
|---|---|---|
| 0 契约确认 | 0–1 min | 读 `/input/case.json` + `task.md` → 记下 case_id、output_contract、材料清单 |
| 1 骨架落盘 | 0–3 min | `python3 -B scripts/main.py run` → 立即写合法 `result.json`（status=partial）+ `corpus.json` + `evidence.json` 候选观察 + `plan.md` + `report.md`。**此后任何中断都已有合法产物** |
| 2 逐条判定 | 3–14 min | 读 `evidence.json` 候选 + 原文 → 逐条判定 成立/排除/降级/待复核，补查遗漏，每条写全四要素与依据绑定 |
| 3 增量合并 | 随步骤 2 | 把判定结果写入 `/app/work/findings.json` → `python3 -B scripts/main.py merge` 更新 result.json 并重建 report.md，change_log 追加 |
| 4 自检收敛 | 最后 1–2 min | `python3 -B scripts/validate.py` → 修 FAIL；确认契约字段齐备、quote 可回查；预算不足时如实 `partial`，不编造 |

前 3 分钟必须先落盘一版可解析的 result.json 骨架；之后只做**增量改进**，任何时刻 result.json 都保持合法 JSON。

## 5. 判定规则（详见 references/audit-rules.md）

- 类型：`fact_error`（事实错误）、`insufficient_evidence`（证据不足）、`norm_violation`（规范/合规问题）、`suggestion`（建议）；严重度 `critical/major/minor/info`。
- 十个检查面：学术诚信、引用规范、隐私、版权、伦理、投稿要求 + 质量/统计/方法/逻辑。
- 每条问题四要素：原文位置（file+locator+quote）、判定依据（basis）、严重度、可执行修改建议。
- **依据绑定**（详见 references/authority-registry.md）：`basis.kind` 只能取 `material_quote/general_principle/target_journal_guide/dataset_license/ethics_policy/unavailable`；拿不到权威依据就写 `unavailable` 并把严重度降为 minor/info，标 `pending_human`。**禁止编造条款、期刊要求、批件号或文献元数据。**
- **误报抑制**：脚本产出的是候选观察（candidate），必须 Agent 逐条判定；风格差异、学科惯例、个人表述不算违规；冲突信息同时记录并标 `source_conflict`；无法确认标 `pending_human`/`not_reported`。

## 6. 自检清单（对应评分维度）

- [ ] `result.json` 可解析，`case_id` 非空，`status` 诚实且 ∈ 允许集合
- [ ] `output_contract` 全部字段存在（缺值 null/[]，未改名未删键）
- [ ] `sources`/`uncertainty`/`change_log` 均为数组
- [ ] 每条 finding 含全部必需字段；类型四分类、严重度分级、verdict 判定正确
- [ ] 每条 `quote` 能回查原文（validate.py 通过）；locator 能解析到对应段落
- [ ] `basis` 不编造依据；unavailable 时已降级并标 pending_human
- [ ] 十个检查面逐项有结论，未只挑最容易的一条
- [ ] 材料缺失/解析失败/软件缺失已写入 `errors`/`degradations`
- [ ] `report.md` 存在可读并回指 result.json；`change_log` 记录每次增量改动
- [ ] 同一输入重跑结论基本一致（脚本确定性、id 哈希稳定、排序固定）

## 7. 降级策略

- 缺 PDF 文本：依次 pdftotext → 平台 pdf-text-tool → ocr-tool；仍不可得 → `insufficient_evidence`/`partial` + errors 记录，只覆盖已解析文本，不猜内容。
- 坏 JSON/坏编码：记录到 errors/degradations，继续处理其余材料；result.json 仍写合法。
- 缺 `output_contract`：用标准 schema，标 `contract_missing=true`。
- 缺软件/工具：如实标注 limitations，不伪造运行结果、评测分数、软件状态。
- 空输入目录：退出码 0，`status=insufficient_evidence`。
- 受限数据：只标位置与类型，不复述、不扩散敏感内容，拿不准标 `pending_human`。

## 8. 脚本调用（相对路径，标准库 only）

在 Skill 包根目录（SKILL.md 所在目录）执行：

```bash
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --output /app/output --work /app/work --findings /app/work/findings.json
python3 -B scripts/main.py report --output /app/output
python3 -B scripts/validate.py --input /input --output /app/output --work /app/work
```

`work/findings.json` 的写法见 `work/plan.md`（run 后自动生成）；示意如下（仅示意，非正式答案）：

```json
{"findings": [{"type": "suggestion", "severity": "info", "category": "quality",
  "title": "示例", "file": "/input/paper.md", "locator": "para 1",
  "quote": "示例原文片段", "candidate": "候选观察", "verdict": "confirmed",
  "adjudication": "判定理由", "evidence": [], "revision": "可执行修改建议",
  "basis": {"kind": "general_principle", "status": "general_principle",
    "source": "skill:audit-rules.md", "text": "gp-citation-verify: ..."}}],
 "coverage": [], "uncertainty": [], "pending_human": []}
```

## 9. 边界

一个 Skill 只解决"材料 → 可复核合规审查报告"这一件事；不追求大而全。无真实材料时输出"材料不足"而非示例填充；交付前在干净环境（不同机器/Python 版本）跑一遍 `run` 与 `validate.py`。
