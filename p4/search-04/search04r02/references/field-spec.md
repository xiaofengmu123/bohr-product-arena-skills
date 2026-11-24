# 字段规范与去重/方向/证据细则

本文件是 `SKILL.md` 的补充细则，供 Agent 与复核人在运行后核对边表、去重键与证据记录。所有字段名与 `scripts/core_impl.py` 中的实现保持一致。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，来自 `/input/case.json`，缺失则状态降 `insufficient_evidence` |
| `status` | string | `ok \| partial \| insufficient_evidence \| input_error`，必须诚实 |
| `seed` | object | 种子论文对象（含 `id/title/first_author/year/doi`…） |
| `seed_key` | object | `type`（`doi/arxiv/title_author_year/title`）、`value`、`confidence` |
| `research_question` | string\|null | 凝练后的研究问题与检索范围 |
| `results` | object | 见 §2 |
| `sources` | array | 每条 `{id,kind,locator,quote,url,accessed_at,status}` |
| `uncertainty` | object | `conflicts[]`、`missing[]`、`coverage_gaps[]` |
| `change_log` | array | `{time,action,reason}`，每次修改必 append |
| `limitations` / `pending_human` / `errors` / `degradations` | array | 状态说明 |
| `runtime` | object | `started_at/ended_at/seconds/model_requests/budget/phases/current_phase` |

`output_contract` 的每个键必须原样回填到顶层；未知键不改名、不删除，缺值给 `null`/`[]`。`bootstrap` 会自动 echo 这些键的占位默认值。

## 2. results 子结构

### 2.1 papers（每条）

```json
{"id":"P0","title":"...","first_author":"...","year":2020,"venue":"...",
 "type":"seed|reference|citation|parallel","doi":null,"arxiv":null,
 "url":null,"source_ids":["s1"],"role":"seed|foundation|bridge|hot|leaf",
 "merged_from":["P1"],"merge_key_type":"doi","merge_key_value":"10.xxx",
 "confidence":"high|medium|low","notes":""}
```

- `type`：`seed` 种子；`reference` 后向被引出处；`citation` 前向施引文献；`parallel` 平行工作（无直接引用）。
- `role`：`foundation` 奠基节点（方法/数据真正出处）、`bridge` 桥接节点（连接不同脉络）、`hot` 近期热点、`leaf` 普通末端。

### 2.2 edges（统一 source cites target）

```json
{"id":"E0","source":"P0","target":"P1","relation":"cites",
 "direction":"directed|unknown","source_label":"...","target_label":"...",
 "evidence_locator":"materials/x.pdf#p3","evidence_quote":"...",
 "source_id":"s1","confidence":"high|medium|low"}
```

方向规则（不得猜测）：

- 后向（种子引用了某篇）→ `source=种子, target=被引文献`，`direction=directed`；
- 前向（某篇引用了种子）→ `source=施引文献, target=种子`，`direction=directed`；
- 数据源只给“被引列表”而不给方向 → `direction=unknown`。

### 2.3 clusters / paths / pruned / ranking

- `clusters`：`{id,label,member_ids[],signal,evidence_locator,evidence_quote}`；`signal` 取值 `cocitation`、`bibliographic_coupling`、`author`、`dataset`、`task` 之一，且必须写明依据。
- `paths`：`{id,start,end,distance,method,hops[]}`；`hops` 逐跳 `{from,to,relation,direction,locator,quote}`，区分直接引用与间接可达。
- `pruned`：剪枝记录，写明节点与剪枝原因。
- `ranking`：`{id,score,components{...},rationale}`；分数分解可复核（距离 0.50、图内流入 0.15、近年 0.15、置信度 0.20，权重见 `core_impl.rank_papers`）。

## 3. 身份合并（去重）规则

优先级固定为：

1. `DOI`（归一化：去前缀、小写、去空白）；
2. `arXiv id`（提取 `\d{4}.\d{4,5}`，忽略版本号）；
3. `title + 第一作者姓 + 年份`（title 去标点、小写、压缩空白；第一作者取姓）；
4. `title`（最弱，仅在没有更强标识的论文之间使用）。

合并后：补充字段以最丰富记录为准，`source_ids` 取并集，写 `merged_from`、`merge_key_type`、`merge_key_value`，并更新 `edges/clusters/paths` 中的 id 引用。

**同名不同文防御**：同一归一化标题、但 DOI/arXiv id 互不相同的记录**不自动合并**，记入 `uncertainty.conflicts`（`kind=possible_duplicate_not_merged`）待人工确认。这是刻意选择“宁漏勿错”，比误合并更可复核。

## 4. 证据溯源细则

- 每个关键结论/边/数值/角色判定都需 `locator` + 逐字 `quote`。
- `locator` 形如 `materials/<file>#p<page>` 或 `crossref#<record>` / `europepmc#<id>`；必须能回指到实际读取的材料或接口响应。
- `quote` 必须逐字照抄来源片段；多个来源一致时择最具代表性的一条，并注“also: ...”。
- 冲突信息两条都记并标 `source_conflict`；缺失标 `not_reported`；不确定标 `low` 置信度。
- 不虚构 DOI、arXiv、引用次数、出版信息；拿不到就不写，写 `pending_human`。

## 5. 确定性保证

- 排序、合并、图布局无随机源；SVG 布局由 `case_id` 的节点距离与稳定 id 顺序决定。
- 时间戳通过 `CITATION_FIXED_TIME` 环境变量可固定，便于公平对照（`scripts/selftest.py` 已覆盖字节级一致性）。
- 同一输入两次运行，数据内容一致；仅真实时钟字段在未固定时间时不同。

## 6. 状态判定参考

| 情形 | status |
|---|---|
| 种子已核实、扩展与证据完整、无重大缺口 | `ok` |
| 完成部分扩展但有覆盖缺口或少数字段缺元数据 | `partial` |
| 缺种子/缺引文记录/材料为空，无法完成追踪 | `insufficient_evidence` |
| 输入无法解析（坏 JSON、缺文件、编码异常） | `input_error` |