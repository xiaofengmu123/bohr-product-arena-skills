# 引文追踪规则手册（RULES.md）

本文件是 SKILL.md 的细则，供 Agent 在执行与复核时逐条对照。SKILL.md 与本文冲突时，以更严格的一方为准。

## 1. 方向与边的规范

- 每条边统一 `source` cites `target`：`source` 是施引方，`target` 是被引方。
- 后向扩展：种子作为 `source`，其参考文献作为 `target`。
- 前向扩展：种子作为 `target`，施引文献作为 `source`。
- 数据源只给“参考文献列表”却不给方向时，写 `relation: "direction_unknown"`，**禁止**按年份或行文顺序猜测方向。
- 每条边至少带：`source`、`target`、`relation`、`source_label`、`target_label`、`evidence`（`locator` + 逐字 `quote`）。
- 撤稿（retraction）与更正（correction/erratum）必须保留为独立节点或独立状态字段，不得与被撤稿原文静默合并。

## 2. 身份合并与去重规则

合并优先级（从强到弱）：

1. DOI（规范化为小写、去 `https://doi.org/` 前缀、去尾随标点）；
2. arXiv id（去版本后缀 `vN`）；
3. 标题 + 第一作者姓 + 年份；
4. 标题（仅在前三类都缺失时使用，且需人工复核同名不同文）。

执行要求：

- 每次合并保留合并证据：`merged_ids`、`n_merged`、`identity_evidence`（成员、键类型、键值）。
- 预印本 / 会议版 / 期刊版 / 增补版视为同一工作，合并为一条主记录，版本信息放入 `versions` 数组，不静默丢弃。
- 同名不同文、同文不同名必须识别：前者不得按标题合并，后者需按 DOI/arXiv 对齐。
- 确定性：处理顺序与代表记录选择稳定（见 `citation_lib.dedupe_papers`）。

## 3. 证据溯源

- 每个关键结论、每条边、每个数值都带 `locator` + 逐字 `quote`。
- `locator` 形如 `materials/<file>#p<N>` 或数据源 URL + 路径；`quote` 是从来源**逐字复制**的片段，不得改写。
- 冲突信息两者都记并标 `source_conflict`；缺失标 `not_reported`；无法判断标 `direction_unknown`。
- 不虚构 DOI、URL、数值、引用或分数；找不到的证据写 `pending_human`。

## 4. 扩展、剪枝与停止

- 层级上限默认 3；到达上限或本轮无新节点即停止。
- 剪枝维度：相关性、年份、文献类型；剪掉的节点与原因写入 `results.pruned`。
- 平行工作（共被引 / 文献耦合 / 作者·机构 / 数据集·任务关联）必须写明关联依据与信号类型，主题相似 ≠ 方法相关。

## 5. 排序

- 用 `citation_lib.rank_papers`：权重依次为 到种子距离、引用度、年份新近度、来源置信度。
- **高被引 ≠ 高相关**：距离与来源置信度权重大于被引数。
- 输出保留 `rank` 与 `score` 供复核。

## 6. 状态与降级

| 状态 | 触发条件 |
|-|-|
| `ok` | 核心追踪完成、关键边有据、去重干净 |
| `partial` | 已交付部分结果但存在缺口 |
| `insufficient_evidence` | 材料不足或引文记录缺失，无法可靠扩展 |
| `input_error` | 无法解析输入（缺 case.json / 坏 JSON 等） |

降级要求：缺数据不补造；缺软件标 `limitations.preview_missing`；限流/权限记 `errors`/`degradations`；任何异常保留已落盘的 partial result.json。

## 7. 鲁棒性红线（本版重点）

- 空输入目录：退出码 0，写合法 `result.json`（`input_error`），不抛异常。
- 坏 JSON / 坏编码：捕获并记 `errors`/`degradations`，继续处理其余材料。
- 缺参数 / 未知参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
- 超大文件：单文件 >2MB 跳过并记录；目录扫描设 1000 文件上限。
- 写盘失败：降级到最小合法产物，进程不崩溃。
- 重复运行：同一输入两次运行（忽略时间戳字段）结果一致。

## 8. 交付前自检

在**不属于作者的环境**（不同机器/Python 版本）跑一遍：

```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
```

全部通过、且 `python3 -B` 无缓存依赖（纯标准库）再交付。
