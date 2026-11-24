# 方法细则：边方向、身份合并、排序与不确定性分类

本文件是 SKILL.md 第 5、6 节的可执行细则，脚本 `scripts/core_impl.py` 与本文一致。所有规则以"可复核、确定性、诚实降级"为第一优先级。

## 1. 边方向规范

统一表示：`source` cites `target`（source 的参考文献列表里出现 target）。

| 场景 | source | target | `relation` | `direction` |
|---|---|---|---|---|
| 种子论文的参考文献（后向） | 种子 | 被引出处 | `cites` | `backward` |
| 引用种子的论文（前向） | 施引论文 | 种子 | `cites` | `forward` |
| 数据源只给被引列表、不给方向 | 未知/不填 | 已知方 | `cites` | `direction_unknown` |
| 共被引关联 | 论文 A | 论文 B | `cocites` | `parallel` |
| 文献耦合关联 | 论文 A | 论文 B | `couples` | `parallel` |
| 作者/机构/数据集/任务关联 | 论文 A | 论文 B | `associates` | `parallel` |

严禁把 `direction_unknown` 猜成 forward/backward；缺方向就是缺方向。非引用关联边必须注明关联信号（写入 `evidence.quote` 或 `notes`），不能只写"主题相似"。

## 2. 论文身份键（由强到弱）

1. `doi`：小写、去掉 `https://doi.org/` 与 `doi:` 前缀；
2. `arxiv`：小写、去掉 `arxiv.org/abs/` 前缀、去掉版本号 `vN`；
3. `标题 + 第一作者姓 + 年份`：标题去标点/空白归一，作者取首作者末 token（近似姓）；
4. `标题`：仅去标点/空白归一。

合并时**保留非空字段、取更强置信度**（`confirmed > verified > inferred > unverified > not_reported`），证据按 `locator` 并集，合并证据写 `merged_from`（成员 id + 键类型 + 键值）与 `merge_log`。同名不同文绝不因标题相同而错误合并：第 3、4 级键只在无更强键冲突时使用。

## 3. 排序公式

`rank_score = relevance_weight[relevance_class] − min(distance, 5)×6 + confidence_score + min(recency, 5)`

- `relevance_weight`：`method_source 100 / data_source 95 / foundational 90 / follow_up 85 / critique 80 / parallel 70 / bridge 60 / hotspot 50 / thematic 40 / other 20`。
- `distance`：相对种子的无向 BFS 层数（仅 `relation=cites` 的边计入）。
- `confidence_score`：`confirmed 5 / verified 4 / inferred 3 / unverified 2 / not_reported 1`。
- 若论文带显式 `rank`，显式 rank 优先（升序），再按 `rank_score` 降序、`id` 升序打破平局。

排序不把"高被引"当"高相关"；被引次数本身不进入公式，只作为可选 `notes`。

## 4. 扩展与停止判据

- 层数上限默认 3（种子为第 0 层）；每层扩展只保留与方法/数据脉络相关的节点。
- 剪枝信号：年份过旧且与当前方法无关、文献类型为编者按/勘误公告（除非涉及撤稿更正）、主题弱相关且无引用路径可达。
- 无新节点即停；剪枝原因写入 `results.pruned`（含 `id`、`reason`、`from_layer`）。

## 5. 不确定性分类（`uncertainty.type`）

| type | 含义 | 处置 |
|---|---|---|
| `coverage_gap` | 数据源覆盖缺口（如新预印本无前向引用） | 写 `affects`+`resolution`，不补齐虚构 |
| `source_conflict` | 同一指标两来源不一致 | 两者都记，标 `source_conflict` |
| `direction_unknown` | 只给被引列表不给方向 | 边标 `direction_unknown` |
| `unverified` | 有线索但未核验 | 置信度 `inferred/unverified` |
| `missing_metadata` | 缺 DOI/年份/卷期等 | 标 `not_reported` |
| `retraction_correction` | 撤稿或更正 | 论文备注 + `pending_human` |

`limitations` 记录数据源能力边界与软件缺省（如 `preview_missing`）；`pending_human` 记录需要人工复核才能定论的事项。三者不混用。

## 6. 证据要求

- 每个关键结论/边/数值必须有 `locator`（相对输入目录的路径 + 可选 `#页码`）+ 逐字 `quote`。
- `locator` 示例：`materials/seed.pdf#p3`、`input/case.json`、`materials/refs.json`。
- 逐字 quote 必须来自实际读到的文本，不缩写、不改写；确实没有来源时写 `not_reported`，不编 locator。
