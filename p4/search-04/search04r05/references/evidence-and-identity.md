# 证据溯源、身份合并与方向规则

本 Skill 的“实际效果”与“结果可核验性”取决于三条硬规则是否被遵守。
脚本负责确定性地执行，Agent 负责按材料如实填写。

## 1. 证据溯源（每个关键结论都要可回指）

- 每条关键结论（种子身份、每条文文献、每条引用边、每个数值、每条路径跳）
  都要给 `locator` + 逐字 `quote`。
- `locator` 形如 `materials/paper.pdf#p3`、`materials/record.json#items/3`；
  由 Agent 按实际材料文件与位置填写，脚本不编造。
- `quote` 必须是材料中逐字出现的片段，不能是转述或猜测。没有材料支撑就写
  `not_reported`，并把该项放进 `uncertainty.missing` 或 `pending_human`。
- 冲突信息两个来源都记录，并标 `source_conflict`（如跨库字段不一致、
  DOI 与标题匹配不上），不隐藏不利证据。
- 每个 `evidence` 结构为：
  ```jsonc
  {"locator": "materials/x.pdf#p3", "quote": "…", "source": "可选", "conflict": false}
  ```

## 2. 边方向约定

- 统一语义：`source` cites `target`。
  - 后向引用 = 种子为 source，其参考文献为 target；
  - 前向引用 = 施引文献为 source，种子为 target。
- 外部数据只给“被引列表”却不给方向时，`relation` 写 `cites`、`direction`
  写 `direction_unknown`，并把原因写入 `uncertainty.notes`，**不得猜测方向**。
- 每条边需含 `source_label` / `target_label`（可读名称）与 `evidence`，便于
  他人不打开原始库也能复核。

## 3. 去重与身份合并

- 身份合并键优先级（高到低）：`DOI`（规范化：去 `https://doi.org/`、`doi:`、
  大小写）→ `arXiv id` → `标题 + 第一作者姓 + 年份` → `标题`。
- 同一论文的预印本 / 会议版 / 期刊版 / 增补版应合并为一个 `papers[]` 条目，
  合并证据写入该条的 `merged[]`（含 `from_id`、`key_type`、`key_value`）。
- 同名不同文、同文不同名要靠上述键之外的信息区分；无法确定时保留两条并记
  `pending_human`，**不强行合并**。
- 撤稿与更正：若材料标明 `retracted` / `correction` / `erratum`，如实写进该
  条文文献的 `status` 字段并在 `limitations` 说明，不当作普通有效文献处理。

## 4. 排序与路径（明确不是“高被引 = 高相关”）

- `rank_papers` 的分数由五部分合成：溯源距离（种子 0 层最高，逐层衰减）、
  图内被引度（上限截断）、图内施引度（桥接贡献）、年份近期小幅加成、
  证据置信（有 `quote` 的证据数）。排名结果写 `rank_distance` 与 `rank_score`。
- 该分数只用于“与种子的引用/关联距离”排序，不声称“高被引=高质量”。
- `shortest_path` 为方向感知 BFS，只沿 `source cites target` 走；路径逐跳标注
  `from / to / relation / direction`，区分直接引用与间接可达。

## 5. 剪枝与停止判据

- 扩展层数默认 ≤ 3；按相关性、年份、文献类型剪枝，剪枝原因写入
  `results.pruned[]`。
- 一层后无新节点即停止；覆盖缺口（如某底层参考文献在库中无记录）写入
  `uncertainty.coverage_gaps`，不得为补齐而编造记录。

## 6. 高效增量写法

Agent 每发现一批事实就用 `update` 落一批指令（`set` / `append` /
`upsert_paper` / `add_edge` / `merge_papers` / `dedupe` / `set_status` /
`set_contract`），脚本自动做去重、方向检查、契约重算与 change_log 追加，
从而把“人工/模型反复核验”压缩为“脚本一次规范 + 只读证据”。