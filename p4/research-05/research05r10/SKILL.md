---
name: research-selection-audit
description: 科研选题与创新性评估 Skill。把固定研究问题拆成可检验表述，对已有工作建立可比较证据矩阵，逐条判定创新点覆盖与可行性前提，所有结论回链到材料逐字片段（source_id+locator+quote），并由 build 脚本做引用完整性与引文逐字校验。当需要对研究选题做立项/投稿前评估、文献证据综述与边界说明时使用。
version: 1.0.0
---

# 科研选题与创新性评估 Skill

## 1. 角色与适用场景

**角色**：立项/投稿/申报前的选题评估执行者。只依据 `/input` 中实际材料下判断，把每个结论落到可回查的证据位置，不替作者补造材料。

**何时使用**：给定研究问题 + 一组文献/材料，需连续回答四件相连的事——问题如何拆成可检验表述、已有工作做到哪一步、增量在哪里、现有条件能否做成；并把证据回链、冲突证据、综合结论与研究空白一并交付。

**不适用**：一次性脚本；纯聊天问答；材料为零却要求给结论的场景（此时返回 `insufficient_evidence`，不编造）。

## 2. 输入契约

运行时固定输入位于 `/input`：

- `/input/case.json`：任务元信息与 `output_contract`（若有，全部键必须原样回显，并同时落到 result.json 顶层）。
- `/input/task.md`：任务说明与评分要点。
- 材料文件：PDF、`.md/.txt/.json/.csv/.xml/.html/.yaml/.bib`、Office 二进制等，按扩展名识别，不假设固定文件名。

硬规则：文本一律容错读取（`utf-8-sig`→`utf-8`→`latin-1` 兜底），坏 JSON/坏编码记入 `errors/degradations`；单文件 >2MB 跳过并记录；不凭标签猜内容，实际读文件。`case.json` 缺失或输入目录缺失 → `input_error`；有输入但无任何材料文件 → `insufficient_evidence`。

## 3. 输出契约

必产 `/app/output/result.json` 与 `/app/output/report.md`。result.json 顶层字段：

- `case_id`（保留输入标识；缺省回退 `unresolved-<输入目录哈希>`）
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实取值
- `output_contract`：原样回显；其每个键同时回填到顶层，缺值给 `null`，未知键不改名不删除
- 主产物：`research_question`、`search_protocol`、`prior_work`、`novelty`、`feasibility`
- `evidence_matrix`（每条含 `id/source_id/locator/quote/field/status/quote_check/note`）
- `conflicts`、`synthesis`（`conclusions/research_gaps/boundary_statement`）
- `sources`、`uncertainty`（`bias/gaps/unresolved/limitations`）、`pending_human`、`errors`、`degradations`、`change_log`、`runtime`、`validation`

每个关键结论必须带 `source_id + locator + 逐字 quote`；无来源写 `not_reported`，缺定位进 `pending_human`，不编 DOI/数值/分数。

## 4. 架构与中间表示（差异化设计）

本包采用**“证据账本 + 声明式规格 + 单一编译器”**，把“事实”与“结论”分表管理：

1. **证据账本** `/app/work/ledger.jsonl`：追加式 JSON Lines，每行一条证据 `{id, source_id, locator, quote, facet, status, note}`，是全部事实的唯一来源。
2. **声明式规格** `/app/work/spec/*.json`：四项交付 + 综合/不确定性/冲突/meta 写成独立 fragment，文件名即 result.json 顶层键（`question.json→research_question`、`prior.json→prior_work`、`novelty.json→novelty`、`feasibility.json→feasibility`、`synthesis.json→synthesis`、`uncertainty.json→uncertainty`、`conflicts.json→conflicts`、`meta.json→status/limitations/pending_human`）。
3. **单一编译器** `python scripts/main.py build`：合并规格与账本，按固定顺序校验——结构完整性 → 材料清单 → 引用完整性（novelty/feasibility/synthesis 里的 `evidence`/`evidence_ids` 必须命中账本 id）→ **逐字引文校验**（quote 在抽取文本中做空白归一子串匹配，产出 `quote_check`）→ 状态一致性 → 落盘。

这样“可核验性”不是自述，而是 build 的机械校验：引文对不上会进 `validation` 与 `pending_human`，而不是静默通过。

## 5. 执行流程（总预算 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

**请求纪律**：一次读一段，不整本灌入一个响应；写完一项立即落盘；同一文件不反复重读。

### Phase 0（0–3 min，最先执行）：骨架先行

```bash
python scripts/main.py init --input /input --output /app/output --work /app/work
```

写 `result.json`（status=partial，全部契约字段就位）+ 空账本 + `spec/`。此后任何失败都至少有一份合法 partial 结果。

### Phase 1（3–6 min）：发现与抽取

```bash
python scripts/main.py scan --input /input --output /app/output --work /app/work
```

扫描材料、抽取 PDF/文本到 `/app/work/text/`，写 `material_inventory.json`，刷新 `sources`；无任何材料文件则置 `insufficient_evidence`。随后读 `task.md` + `case.json` 确认四项交付是否全部要求。

### Phase 2（6–11 min）：四项交付 + 证据入账

读完一段就追加证据：

```bash
python scripts/main.py evidence --source <id> --locator "..." --quote "..." \
  --facet prior_work|novelty|feasibility|conflict|gap --status fact|inference|conflict
```

每完成一项交付就写一个 fragment 到 `/app/work/spec/`，键名与 result.json 一致：

1. **研究问题**：对象/关键变量/比较基线/预期结论/可证伪条件 → 可检验陈述；缺口标 `partial/missing`。
2. **已有工作**：任务/方法/数据/评价方式/结论矩阵；标不可比原因；禁跨文档串值。
3. **创新点**：方法/数据/场景迁移/结论修正；每条 `verdict ∈ novel|covered|partially_covered|unverifiable`，给 `covered_by` + `evidence_ids`。
4. **可行性**：数据/算力/实验条件/时间窗口/人员能力；每条前提标 `reported|assumed|missing|conflicting`。

### Phase 3（11–14 min）：综合与不确定性

写 `synthesis.json`（conclusions/research_gaps/boundary_statement）、`conflicts.json`（两方记录 + 取舍理由）、`uncertainty.json`（bias/gaps/unresolved/limitations）、`meta.json`（status/pending_human/extra_sources/limitations）。每次改动用 `python scripts/main.py log --msg "..."` 登记。

### Phase 4（14–15 min）：编译、校验、收尾

```bash
python scripts/main.py build --input /input --output /app/output --work /app/work
```

合并规格 + 账本 → 校验 → 写最终 `result.json` 与 `report.md`。剩余时间按 `validation` 报错做增量修正后重跑 build。

## 6. 自检清单（对应评分维度）

- [ ] 可检验问题：`research_question.testable_statement` 含对象/变量/基线/可证伪条件。
- [ ] 四项交付齐全一致，引用同一账本 id 集。
- [ ] 六类内容覆盖：问题拆解、检索覆盖、证据回链、冲突、综合结论、研究空白。
- [ ] 可核验：每条结论在 evidence_matrix 有 locator + 逐字 quote；检索式/纳排规则写入 `search_protocol`。
- [ ] 创新边界：每条 novelty 有 `covered_by` + `verdict`，场景迁移/结论修正未当方法创新。
- [ ] 可行性前提有出处：`reported|assumed|missing|conflicting`，assumed 显式标注。
- [ ] 冲突不隐藏：进 `conflicts` 并说明取舍；“未报告”≠“不存在”。
- [ ] 诚实状态：status 与 errors/degradations/pending_human/limitations 一致。
- [ ] 骨架先行：Phase 0 在 3 min 内落盘 result.json。
- [ ] 可复跑：同输入两次 build 结论一致（除 runtime 时间戳）；change_log 记录改动。
- [ ] 自检通过：`python scripts/selftest.py` 全部 PASS（空输入/坏 JSON/缺输入/引文校验/确定性）。

## 7. 降级与诚实性

- 缺数据：对应字段留 `null/[]` 并写入 `uncertainty.gaps` 或 `limitations`，status 降级；不编数据/DOI/分数/运行结果。
- 缺软件：`pdftotext` 不可用时 `scan` 在 `degradations` 记录，PDF 标 `text_unavailable`，继续处理其余文本。
- 引文对不上：build 把该证据标 `quote_check=not_found/too_short/text_unavailable`，进 `validation` 与 `pending_human`，不删除。
- 冲突/偏倚/空白：登记 `conflicts` 与 `uncertainty`；推断标 `inferred`，不写成事实。
- 写盘失败：捕获异常，降级写最小合法结果，进程不崩溃。

## 8. 脚本与资源

- `scripts/main.py`：CLI 入口，子命令 `init/scan/evidence/log/build/selftest`；`--help` 退出码 0。
- `scripts/core.py`：契约回填、材料发现、PDF 抽取、账本、规格、编译与校验。
- `scripts/selftest.py`：合成输入自检，不触碰正式案例。
- `references/assessment-rules.md`：四项交付判定口径、证据字段与词汇表。
- `references/checklist.md`：可勾选自检与评分维度对照。

调用一律相对路径 `scripts/*.py`；`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。全部脚本仅依赖 Python 标准库。
