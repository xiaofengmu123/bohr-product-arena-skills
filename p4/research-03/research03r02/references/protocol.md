# 系统性文献综述协议与数据模式

本文件是 `SKILL.md` 的规则细则，供 Agent 写 `/app/work/` 中间文件时逐字段对照，也供他人在其他综述任务中复用（检索式模板、纳排标准、筛选表、证据表、质量量表）。所有字段名与 `scripts/core_impl.py` 的组装逻辑一致，最终由 `python -B scripts/main.py finalize` 确定性合并进 `/app/output/result.json`。

## 1. result.json 字段模式

领域字段（`output_contract` 未声明时按此补齐；已声明的键原样保留）：

```text
review_question   string         单一研究问题，可含 PICO/PECOT 要素
subquestions      list[obj]      每项 {id, text, depends_on[]}；depends_on 表示依赖的子问题 id
search_strategy   {query_blocks: list[obj]}
inclusion_criteria  list[obj]    {id, text, priority}
exclusion_criteria  list[obj]    {id, text, priority}
screening_records list[obj]      题录 + 全文两轮，见 §2
prisma_flow       {stages:{title_abstract:{screened,included,excluded}, fulltext:{...}}, included_total}
quality_appraisal list[obj]      见 §3
synthesis         {conclusions:[], conflicts:[], gaps:[]}  见 §4
evidence_map      list[obj]      见 §5
uncertainty       list[str|obj]  冲突、偏倚、空白、无法确定项
```

诚实字段（脚本固定追加，勿删）：`case_id, status, sources, uncertainty, change_log, limitations, errors, degradations, pending_human, runtime_seconds, artifacts`。

## 2. screening_records（JSONL：每行一条判定）

```json
{"record_id":"R1","stage":"title_abstract","decision":"include|exclude",
 "criterion_id":"I1|E3","reason":"仅 exclude 必填","judge":"agent",
 "duplicate_of":"R0","source":"题录或文件名","abstract_only":false}
```

- `stage` 两轮：`title_abstract`（题录/摘要）与 `fulltext`（全文）；同一篇可各有一行。
- `decision`：`include` / `exclude`（另有 `pending` 表示本轮暂缓）。
- 每条 `exclude` 必须给 `reason` 与 `criterion_id`；`reason` 引用事先条款而非事后补写。
- `judge` 记录判定人（agent / 双人则记录 `judge_a`、`judge_b` 及 `disagreement`）。
- 去重：重复记录标 `duplicate_of` 指向首次出现的 `record_id`，不计入独立纳入数。

## 3. quality_appraisal（JSONL：每行一篇纳入研究）

```json
{"record_id":"R1","tool":"自定义四级量表","overall":"high|moderate|low|unclear",
 "impact":"纳入结论/仅作背景/降权","dimensions":[
   {"dim":"随机化","rating":"low|high|unclear","basis_locator":"study.md#结果","quote":"随机分组。"}]}
```

- 逐维度给 `rating` + `basis_locator` + 逐字 `quote`，总体给 `overall` 并写清对结论的 `impact`。
- `overall` 不建议纯平均：关键维度（如随机化、失访）可加权；评级规则写进 `protocol.json` 的说明字段。

## 4. synthesis（JSON）

```json
{"conclusions":[
   {"text":"…","evidence_ids":["EV1","EV2"],"confidence":"established|likely|suggestive|not_reported"}],
 "conflicts":[{"text":"…","sources":["EV1","EV3"],"resolution":"undetermined"}],
 "gaps":[{"gap":"缺少长期随访","locator":"材料未报道"}]}
```

- 每条结论必须带 `evidence_ids` 且命中 `evidence_map` 的 `id`；`confidence` 按证据强度如实降级。
- 冲突逐条并列至少两个来源；无法裁定标 `resolution=undetermined`，不强行取一方。
- 空白落到具体缺口（人群、时间窗、结局、全文可得性等），不写空泛的“证据不足”。

## 5. evidence_map / evidence_cards.jsonl（JSONL）

```json
{"id":"EV1","source":"Study A","locator":"study_a.md#结果","quote":"干预组结局优于对照组。"}
```

- `locator` 采用 `文件名#章节/段落/页码`，稳定可复核；`quote` 必须逐字取自材料，不得改写。
- `id` 固定且唯一；`finalize` 后 `evidence_map.json` 与 result.json 的 `evidence_map` 同源。

## 6. 检索式模板（可复用）

每个 `query_blocks` 条目：

```json
{"database":"PubMed","fields":"tiab","query":"(X[tiab] OR X[mesh]) AND (Y[tiab])",
 "intent":"核心概念交集，同义词用 OR 覆盖",
 "time_limit":"2010-01-01..2026-01-01","language":"en"}
```

要求：`database` + `query` + `intent` 必填；`time_limit` 与 `language` 至少其一；布尔逻辑明确，同义词/受控词并列，说明每段意图，不退化成一个关键词串。

## 7. 可复核规则

- 固定证据 `id`、固定字段顺序、确定性组装：同一输入两次运行结果一致。
- 纳排标准在筛选决定之前落盘（`protocol.json` 时间戳早于 `screening.jsonl` 判定时间）。
- 任何计数（PRISMA、纳入数）由 `finalize` 从 `screening_records` 重算，不信任手工键入的数字。
- 未知字段保留键填 `null`/`[]`；缺失项写 `not_reported`，不编造文献/DOI/数值。
