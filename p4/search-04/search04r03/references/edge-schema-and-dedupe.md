# 边表字段与去重规则（可复用规范）

本文件是 `result.json` 中 `results.papers` / `results.edges` / `sources` 的
字段规范与去重/身份合并规则，供 Agent 增量写回时遵循，也供他人复用。

## 1. papers 条目

```json
{
  "id": "p-<稳定短标识>",
  "title": "论文标题（原样）",
  "authors": [{"family": "姓", "given": "名"}, "或字符串"],
  "year": 2020,
  "doi": "10.xxxx/yyyy",
  "arxiv_id": "2001.01234",
  "venues": ["会议/期刊名"],
  "versions": ["p-a-pre", "p-a-jour"],
  "role": "foundation | bridge | seed | hotspot | parallel | null",
  "layer": 0,
  "rank_score": 12.34,
  "sources": [{"locator": "...", "quote": "..."}]
}
```

- `id` 稳定且确定性：优先 `p-<第一作者姓>-<年份>-<短标题>`，冲突时追加序号；
  不依赖检索顺序，保证同一输入两次运行 id 相同。
- `layer`：种子为 0；沿引用边 BFS 得到的跳数；远端取 99。
- `versions`：身份合并后被并入的成员 id 列表（含自身）。

## 2. edges 条目（统一为 `source` cites `target`）

```json
{
  "source": "p-seed",
  "target": "p-method",
  "relation": "cites",
  "direction": "backward | forward | direction_unknown",
  "layer": 1,
  "source_label": "种子标题",
  "target_label": "出处标题",
  "evidence": [{"locator": "...", "quote": "..."}]
}
```

- 后向 = 种子为 source；前向 = 种子为 target；数据源不给方向时标
  `direction_unknown`，**不得猜测**。
- 同 `source/target/relation` 只保留一条，证据并集（脚本自动 dedupe_edges）。

## 3. sources / evidence 条目

```json
{"locator": "materials/refs.json#p3", "quote": "逐字原文", "kind": "crossref", "url": null}
```

- `locator` 是可回指的定位：`文件路径#页码/行号/记录 id`。
- `quote` 是逐字原文，不压缩不转述；缺失标 `not_reported`。

## 4. 身份合并优先级（确定性）

1. DOI（小写、去 `https://doi.org/` 前缀）
2. arXiv id（去版本号 vN）
3. 归一化标题 + 第一作者姓 + 年份
4. 归一化标题 + 年份
5. 归一化标题（近似匹配阈值 0.94，仅作跨库兜底）

同名不同文不合并：title 相同但作者/年份不同时，不得进入第 3/4 键；
同文不同名可合并：DOI 或 arXiv 一致即合并，并保留 `versions` 与合并证据。

## 5. 确定性约定

- 排序键：论文 `(-rank_score, id)`；边 `source->target:relation`；
  簇内成员按 id 升序。
- JSON 写盘：`sort_keys=True, ensure_ascii=False`；浮点 rank_score 保留 4 位。
- `content_hash()` 只对排除 `runtime`/`change_log` 后的确定性子集取 SHA-256，
  供 `selftest` 验证"同输入两次运行产物一致"。
- 图谱 `citation_graph.svg` 用 BFS 分层 + 层内按 id 排序，无随机布局。

## 6. 停止与剪枝

- 层数上限默认 3；无新节点即停。
- 按相关性/年份/文献类型剪枝，剪枝原因写入 `results.pruned`。
- 高被引不等于高相关：距离（layer）占主导，被引仅作同层弱信号。
