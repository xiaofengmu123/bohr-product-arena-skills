# 引文追踪规则库（可复用规范）

本文件是 SKILL.md 之外的可复用规则清单：边表字段、去重键、证据溯源、停止判据。
脚本 `scripts/core_impl.py` 是这些规则的确定性实现，二者保持一致。

## 1. 有向引用边（edges）字段

统一约定：**`source` cites `target`**（source 引用 target）。

| 字段 | 必需 | 说明 |
|-|-|-|
| `source` | 是 | 施引论文稳定 id |
| `target` | 是 | 被引论文稳定 id |
| `relation` | 否 | 默认 `cites`；可扩展为 `rejects` / `corrects` / `builds_on` |
| `direction` | 是 | `cites`（方向明确）；外部数据只给列表不给方向时写 `direction_unknown`，**不得猜测** |
| `source_label` / `target_label` | 否 | 可读短标题，便于 CSV/SVG 展示 |
| `evidence_locator` | 是 | 数据来源定位，如 `materials/x.json#refs[3]` |
| `evidence_quote` | 是 | 逐字引用片段；缺失写 `not_reported` |

后向 = 种子为 `source`；前向 = 种子为 `target`。多层级扩展时每条边独立成行，
层级可写入 `depth` 字段（可选）。

## 2. 论文身份合并（dedupe）规则

身份键优先级（脚本 `paper_identity_keys`）：

1. `DOI`（规范化：去前缀、小写）——最高置信度；
2. `arXiv id`（去 `arxiv:` 与 `/abs/` 前缀、小写，保留老式类别前缀避免误并）；
3. `title + 第一作者姓 + 年份`——区分同名不同文；
4. 仅 `title`——最低置信度，最后兜底。

合并策略：共享任一更高优先级键即视为同一工作（预印本/会议版/期刊版/增补版）。
合并证据必须保留：`key_type`、`key_value`、`member_id`，写入 `papers[].merge_evidence`
与 `results.clusters[]`。被并掉的版本 id 写入 `papers[].aliases`。
规范化的标题/作者名按 NFKD 折叠、小写、去标点、去冠词，保证拼写抖动不造成假重。

同名不同文靠更长的键区分：仅标题相同但一作或年份不同 → 不合并。
同文不同名（大小写、副标题、作者顺序）→ 由长键兜底合并。

## 3. 证据溯源规范

每个关键结论、引用边、数值都必须有 `locator`（定位）+ `quote`（逐字）。规则：

- `locator` 指回实际输入文件，如 `materials/paper.pdf#p3`、`materials/refs.json#10`；
- `quote` 必须逐字复制原文，不转述、不改写；拿不到原文写 `not_reported`；
- 两处来源不一致 → 两处都记录并标 `source_conflict`，写进 `uncertainty`；
- 缺元数据/缺引文记录 → 写 `not_reported` 或 `pending_human`，**不补造**；
- 实际输入文件与原论文是唯一事实依据，不依据题面领域标签猜内容。

## 4. 扩展与停止判据

| 判据 | 规则 |
|-|-|
| 层数上限 | 默认 3 层；远端文献无需继续时提前停止 |
| 相关性剪枝 | 方法与主题无关的引用边剪掉，理由写入 `results.pruned[]` |
| 年份/文献类型 | 仅扩展现/随论文与有 DOI 的会议/期刊版本，忽略网页资料非正式引用 |
| 无新节点 | 某层未产生新身份即停，避免无意义翻页 |
| 分支出度 | 单节点向外 > 30 条时按年份与出处优先级截断 |

每步都应在 `change_log` 记录「加了什么、为什么、什么没做完」。

## 5. 排序原则（结构排序，不是"高被引=高相关"）

脚本 `rank_papers` 只量化可客观计算的结构与证据置信度：

- `depth`：离种子的引用跳数（越近越可复核，权重最高）；
- `indeg` / `outdeg`：集合内被引/引用次数，仅作结构信号；
- `source_confidence`：`evidence_locator` 与 `quote` 是否齐全；
- 年份：近三年小幅加成。

主题/方法相关度必须由 Agent 依据证据判断，写入 `papers[].relevance_why`，
脚本与 Skill 均不宣称"被引多 = 相关"。

## 6. 状态与诚实性

`status ∈ {ok, partial, insufficient_evidence, input_error}`：

- `ok`：种子已核实、主结论均有证据、产物完整；
- `partial`：部分完成、已明确边界；
- `insufficient_evidence`：材料不足以支撑追踪，写清缺口；
- `input_error`：输入缺失/坏 JSON/坏编码无法解析。

任何异常都先落盘合法 partial 结果再继续；进程退出码 0，不让评测崩溃。