# result.json 字段与台账 schema

本文件定义 `/app/output/result.json` 的全部字段、枚举与一致性规则，供 Agent 填写与 `scripts/check_revision.py` 校验使用。所有路径均为运行时实际路径；`/input` 内用相对定位便于复核。

## 1. 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 从 case.json 原样读取，不得改写 |
| `status` | string | 是 | `in_progress\|partial\|complete\|failed\|insufficient_evidence` |
| `deliverable` | object | 是 | 题目 output_contract 字段 1「目标产物」 |
| `fact_source_mapping` | array | 是 | 题目 output_contract 字段 2「事实与来源映射」 |
| `version_boundary` | object | 是 | 题目 output_contract 字段 3「版本与边界」 |
| `response_letter` | object | 是 | 回复信及其逐条台账 |
| `sources` | array | 是 | 输入材料清单 |
| `uncertainty` | array | 是 | 不确定性条目 |
| `pending_human` | array | 是 | 需人工确认条目 |
| `limitations` | array | 是 | 局限（本版重点字段） |
| `degradations` | array | 是 | 运行降级记录 |
| `change_log` | array | 是 | 落盘/修改日志 |
| `runtime` | object | 否 | 开始时间与预算，用于审计 |

## 2. deliverable

```json
{"path": "/app/output/revised-manuscript.md", "format": "markdown",
 "title": "...", "version": "v2", "sections": ["Abstract", "Methods", "Results"],
 "status": "draft|ready"}
```
- `path` 指向 `/app/output` 下可编辑源文件；`format` ∈ `markdown|docx|txt|json|csv`。
- Office/幻灯片/图表产物另产真实 PDF/PNG 预览，并单独在 `sources` 或正文中说明渲染命令。

## 3. fact_source_mapping 条目

```json
{"claim_id": "R1-A1", "claim": "...", "kind": "number|term|citation|figure|key_claim|revision",
 "source_ref": "materials/manuscript-baseline.md", "location": "Results, para 3",
 "quote": "逐字原文片段", "status": "traced"}
```
`status` 五级（本版核心：可核验性分层）：
- `traced`：quote 能在 source_ref 中逐字找到；**必须带非空 quote**。
- `derived`：由材料计算/归纳得出，需在 `location` 说明推导依据；quote 可给关键片段。
- `assumption`：材料未给出的假设；**必须同步进 `pending_human` 或 `uncertainty`**。
- `unverified`：声称存在但来源未获得，如"原审稿全文"；quote 留空。
- `source_conflict`：两处来源不一致；两处均记录，同一条目 `location` 列出两处定位。

## 4. version_boundary

```json
{"changes": [
  {"id": "C01", "comment_id": "R1", "before": "原文片段", "after": "改后片段",
   "location": "Results, para 3", "anchor": "we observed a 12% increase",
   "manuscript_version": "v2", "status": "applied"}
 ],
 "pending_confirmation": [{"item": "...", "reason": "..."}],
 "material_gaps": [{"item": "...", "impact": "..."}],
 "anomalies": [{"item": "...", "disposition": "..."}]}
```
- `anchor`：改后稿中**仍可检索**的 8–40 字唯一片段，供 check_revision.py 独立核对"改得准"。
- `status` ∈ `applied|pending|reverted|unapplied`；`unapplied` 必须出现在 `pending_human`。
- 每条 `applied` 修改对应一条 `fact_source_mapping`（kind=`revision`），实现"回复→改动→定位"闭环。

## 5. response_letter 条目

```json
{"comment_id": "R1", "decision": "partial-accept", "tone": "clarify",
 "summary": "采纳 X，就 Y 作澄清", "subrequirements": ["R1a", "R1b"],
 "change_ids": ["C01"], "evidence": [
   {"item": "补基线数据", "status": "provided|missing|cannot-provide",
    "source_ref": "materials/manuscript-baseline.md", "quote": "..."}
 ], "unresolved": ["子要求 R1b 需要…"]}
```
- `decision` 与 `tone` ∈ `accept|clarify|rebut|appeal`（accept=采纳/接受，clarify=澄清，rebut=礼貌反驳，appeal=申诉）。
- `partial-accept` 的 decision 表达为 `tone=clarify` 或显式列 `unresolved`；不得写"已完全解决"却留空 `change_ids`。
- 语气规则：不得使用冒犯、过度承诺或无原则顺从措辞；申诉必须给出理由与所需证据，不攻击审稿人。

## 6. 不确定性/局限/降级字段

- `uncertainty`：`{item, reason, disposition}`；disposition ∈ `assumed|deferred|documented|blocked`。
- `pending_human`：`{item, reason, disposition}`；disposition ∈ `need_author|need_data|need_permission|need_expert`。
- `limitations`：`{item, scope, impact}`；写清本结果覆盖与不覆盖的范围（本版重点）。
- `degradations`：`{item, cause, fallback}`；记录跳过 2MB 文件、编码回退、缺软件等运行降级。

## 7. 意见分类法（可复用：把一条意见拆成结构化字段）

每条审稿意见拆为子要求后，逐项标注以下维度，形成可复用的台账字段：

| 维度 | 取值 | 说明 |
|---|---|---|
| 类型 | `method` / `data` / `writing` / `format` / `ethics` | 决定回应动作与证据形态 |
| 严重程度 | `major` / `minor` / `cosmetic` | major 必须落实 diff 或给出不可补的替代说明 |
| 可执行动作 | `revise` / `add-evidence` / `reanalyze` / `add-citation` / `clarify` / `format-fix` | 一条意见可含多个动作 |
| 关系 | `independent` / `depends-on:<comment_id>` | 记录子要求间的依赖，避免只答一半 |

拆解规则：一条意见含 N 个"要求"就产出 N 个 `subrequirements` 条目；若只识别出 1 条但原文明显含多个动作，视为"只答一半"风险，必须在 `unresolved` 列明未处理部分。多个意见互相冲突时，两条都保留并在 `anomalies` 记录 `source_conflict`，不得擅自取舍。

## 8. 一致性不变量（check_revision.py 校验）

1. `traced` 必带非空 quote；`assumption` 必进 `pending_human` 或 `uncertainty`。
2. 每条 `changes` 有 `anchor`；给定改稿后 anchor 必须能在改稿文本中检索到。
3. 每条 `response_letter.entry.change_ids` 指向存在的 change；每条 change 至少被一条 entry 引用（无孤儿改动）。
4. 回复 summary 含"已修改/已完成/已补"且 `change_ids` 与 `evidence` 均为空时，判定为过度声明。
5. 全表 `manuscript_version` 一致，并与 `deliverable.version` 一致。

## 9. 回复信模板（可直接复用，占位符按需替换）

```markdown
# Response to Reviewers — {{manuscript_id}} ({{version}})

Manuscript version referenced below: {{version}} (path: {{deliverable.path}})

## Reviewer {{n}}, Comment {{id}} — {{decision}} ({{tone}})

**Reviewer comment (paraphrased from materials):** {{quote_or_ref}}
**Decision:** {{accept | partial-accept | clarify | rebut | appeal}}

Our response:
{{paragraph stating what was done, using only facts traceable to materials}}

Changes in the revised manuscript:
- [C{{id}}] {{before}} → {{after}} — located at {{location}}; searchable anchor: `{{anchor}}`

Evidence / supplement status:
- {{evidence item}} — status: {{provided | missing | cannot-provide}} — source: {{source_ref}}

Unresolved / pending human confirmation:
- {{item}} — reason: {{reason}} — disposition: {{disposition}}
```

规则：正文不得出现无凭据的"已完全解决"；无法补实验时写"cannot-provide"并说明所需证据，不得改写为"已补做"。

## 10. 语气分级（decision/tone 组合规则）

| tone | 适用 | 禁止 |
|---|---|---|
| `accept` | 意见成立且已采纳并落实 diff | 空话致谢（"感谢宝贵意见"后无动作） |
| `clarify` | 意见部分成立或有误解，需澄清而非改稿 | 把误解当错误，用攻击性措辞 |
| `rebut` | 意见基于错误前提或与材料事实冲突，礼貌反驳 | 冒犯审稿人、绝对化断言（"显然错误"） |
| `appeal` | 对无法补做的要求申诉，说明条件与证据 | 过度承诺将来必然补做，或无理由拒绝 |

每条回复只选一个 tone；`partial-accept` 通常配 `clarify`，并在 `unresolved` 列明未解决子要求。

## 11. 最小合成示例（仅示意 schema，SYNTHETIC，非任何正式案例）

```json
{
  "comment_id": "R1",
  "decision": "partial-accept",
  "tone": "clarify",
  "summary": "We adopted the requested sample-size report and clarify the inclusion window.",
  "subrequirements": ["R1a", "R1b"],
  "change_ids": ["C01"],
  "evidence": [
    {"item": "per-group sample sizes", "status": "provided",
     "source_ref": "materials/manuscript-baseline.md", "quote": "120 participants"}
  ],
  "unresolved": ["R1b: inclusion window needs author confirmation"]
}
```
对应 change：
```json
{"id": "C01", "comment_id": "R1", "before": "no per-group sizes",
 "after": "Per-group sample sizes are 60 and 60.",
 "location": "Results, para 1", "anchor": "Per-group sample sizes are 60 and 60",
 "manuscript_version": "v2", "status": "applied"}
```
