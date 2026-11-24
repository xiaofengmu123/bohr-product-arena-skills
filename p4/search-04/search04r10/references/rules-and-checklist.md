# 规则与检查清单（复用参考）

本文件是 `SKILL.md` 的补充规范，供其他 Agent 或协作者复用：边表字段、去重键、证据定位格式、停止判据与校验不变量。

## 1. 边表字段（edges.csv / results.edges）

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | string | 出边节点 id（引用方），统一为 “source cites target” |
| `target` | string | 入边节点 id（被引方） |
| `relation` | enum | `cites` / `direction_unknown` / `association` |
| `source_label` | string | 引用方可读名 |
| `target_label` | string | 被引方可读名 |
| `evidence` | object | `{locator, quote, source_refs?, confidence?}` |

方向约定：后向追踪 = 种子为 `source`；前向追踪 = 种子为 `target`。外部数据只给被引列表不给方向时，标 `direction_unknown` 且写入 `uncertainty`，不得猜测。非直接引用的平行关联用 `association`，并在 `evidence`/`uncertainty` 说明关联信号（共被引/文献耦合/作者机构/数据集任务）。

## 2. 身份合并键（优先级从高到低）

1. `doi`（归一化：去 `https://doi.org/` 前缀、小写）
2. `arxiv_id`（归一化：去 `https://arxiv.org/abs/` 前缀、小写）
3. `title` + 第一作者姓 + `year`
4. `title`（归一化：去非字母数字、小写）

合并输出：canonical 节点 `id = stable_id()`（DOI → arXiv → title:year → title），多版本写入该 paper 的 `versions`，并在 `results.clusters` 加一条合并证据（canonical_id、members、merged_on）。规则：有 DOI 的任何成员视为同文；同名不同文（DOI/arXiv/作者/年份均不匹配）不合并。

## 3. 证据定位格式（locator + 逐字 quote）

- JSON 输入：`materials/<name>.json#<JSONPointer 风格路径>`，如 `materials/crossref.json#message/reference/0`；
- 文本输入：`materials/<name>.md#<行号或段落号>`；
- PDF 输入：`materials/<name>.pdf#p<页码>`（以解析文本所在页为准）。

硬要求：每个关键结论/每条边/每个从材料取来的数值都要有 `locator` + 逐字 `quote`。冲突信息两个都记并标 `source_conflict`；字段缺失标 `not_reported`。

## 4. 排序与停止判据

- 排序由 `rank_papers` 计算：`score = 100/(1+distance) + max(0, year-1900)/100 + confidence*10`，按 `distance` 升序、`score` 降序、标题拼音序为最终序。高被引 ≠ 高相关，被引数只作旁证。
- 停止：扩展层数默认 ≤3；相关性低/非研究型文献/已见节点剪枝；剪枝原因写入 `results.pruned`；无新节点即停。

## 5. 校验不变量（validate_ir）

- `case_id`、`status`、`results`、`sources`、`uncertainty`、`change_log` 六项必有；`status` ∈ {ok, partial, insufficient_evidence, input_error}。
- `results.papers/edges/clusters/paths/pruned` 均为 list；papers 的 `id` 全局唯一。
- 每条边有 `source`/`target`，`relation` ∈ {cites, direction_unknown, association}，两端 id 必须出现在 papers 或等于种子 id。
- 每条 source 有 `locator` + `quote`；每条边 `evidence` 有 `locator` + `quote`。
- `change_log` 为 list，每条有 `time`。

`skeleton` 模式只校验必需键与状态；`full` 模式额外校验端点、证据与去重。

## 6. 最小调用示例（示意，非正式案例）

```bash
# 1) 建骨架（前 3 分钟内）
python3 scripts/main.py discover --input /input --output /app/output --work /app/work
# 2) 增量注入（每次分析后）
python3 scripts/main.py merge --work /app/work --patch /app/work/patch-01.json --reason "核实种子 + 第一层扩展"
python3 scripts/main.py stage --work /app/work --output /app/output
# 3) 收盘（最后 60 秒）
python3 scripts/main.py finalize --work /app/work --output /app/output
python3 scripts/selftest.py
```

示意 result.json 骨架（标为示例，不冒充正式案例结果）：

```json
{
  "case_id": "demo",
  "candidate_papers": [],
  "status": "partial",
  "seed": {},
  "seed_key": null,
  "results": {"papers": [], "edges": [], "clusters": [], "paths": [], "pruned": []},
  "sources": [],
  "uncertainty": [],
  "change_log": [{"time": "2026-01-01T00:00:00Z", "content": "discover 骨架", "reason": "前 3 分钟落盘"}]
}
```

正式案例、白板与测评结果由平台提供或生成，本包不预置。