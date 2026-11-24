# mindmap_spec.json 规范（render_mindmap.py 的输入）

`render_mindmap.py` 读取 `/app/work/mindmap_spec.json`，把它渲染成 Markdown / Mermaid / GraphML / SVG / 证据映射五类产物。Agent 按本规范写 spec；脚本负责确定性渲染与格式校验。

## 1. 节点类型 kind

节点必须归类到以下某一种，用于区分"事实、概括、推断"并决定 SVG 配色：

| kind | 含义 | 示例 |
|---|---|---|
| topic | 根主题（仅根节点） | 论文整体主题 |
| concept | 核心概念 / 术语 | 铁死亡、拓扑绝缘体 |
| question | 研究问题 | 是否 X 导致 Y？ |
| method | 方法 / 模型 / 实验设计 | 随机对照试验、DFT 计算 |
| evidence | 数据 / 证据 / 结果 | 效应量 0.34，p<0.05 |
| conclusion | 结论 / 发现 | 机制 A 成立 |
| task | 待办 / 任务 | 补做敏感性分析 |
| risk | 风险 / 局限 | 样本量不足 |
| gap | 研究空白 / 未解问题 | 尚无纵向数据 |
| milestone | 里程碑 | 完成第一轮审稿 |
| decision | 决策 / 决议 | 采用方法 B |
| todo | 行动项 | 下周三前提交 |

同一层级的节点应保持相近粒度：不要把"方法"与"某个具体数字"混在同一层（数字应作为 evidence 挂在方法/结论之下）。

## 2. 关系类型 kind（relations）

关系必须有材料依据，方向用 `from` → `to` 表达，`from` 是来源/原因/前提，`to` 是结果/被依赖/结论：

| kind | 含义 |
|---|---|
| causal | 因果：from 导致 to |
| dependency | 依赖：to 依赖 from（from 先发生/是前提） |
| contrast | 对比 / 差异 |
| temporal | 时间先后 |
| citation | 引用：to 引用 from（from 是文献/来源） |
| support | 支持：from 支持 to（证据支撑结论） |
| conflict | 冲突：from 与 to 不一致 |
| part-of | 包含（树结构已隐含，跨树补充时用） |
| is-a | 种属 / 上下位 |

## 3. 证据格式 evidence

每条证据必须含 `locator`（定位，如 `materials/paper.md#p3`、`case.json#task`、`task.md#L12`、`refs/foo.pdf#p7`）与 `quote`（从材料中逐字摘录的原文片段）。quote 必须逐字，不得改写；概括与推断另写进节点 `note`，并用 `note_type: "fact|summary|inference"` 标明。

## 4. 完整 JSON 结构

```json
{
  "title": "根主题（一句）",
  "root": {
    "id": "root",
    "text": "根主题",
    "kind": "topic",
    "note": "",
    "evidence": []
  },
  "nodes": [
    {
      "id": "n1",
      "text": "节点文本（简短，≤40 字）",
      "kind": "concept",
      "parent": "root",
      "note": "需要保留的概括或推断",
      "note_type": "summary",
      "evidence": [
        {"locator": "materials/paper.md#p3", "quote": "原文逐字片段"}
      ]
    }
  ],
  "relations": [
    {
      "from": "n1",
      "to": "n2",
      "kind": "support",
      "label": "可选：一句话说明",
      "evidence": [{"locator": "materials/paper.md#p5", "quote": "原文片段"}]
    }
  ]
}
```

## 5. 约束

- 节点 `id` 全局唯一；`parent` 必须指向已存在的节点 id（缺省视为 `root`）。
- 关系端点必须指向已存在的节点 id；未知 kind 会被规范化为 `support`。
- 无材料的连接一律不要写；写不出证据的连接改成 `pending_human` 记录，不进 spec。
- 冲突信息不隐藏：用两条 relation（`support` 与 `conflict` 各一）或节点 note 同时保留两个来源，标注 `source_conflict`。

## 6. 示意（仅示意，非正式案例结果）

```json
{
  "title": "某方法的效应（示意）",
  "root": {"id": "root", "text": "某方法的效应（示意）", "kind": "topic"},
  "nodes": [
    {"id": "n1", "text": "研究问题", "kind": "question", "parent": "root",
     "evidence": [{"locator": "demo.md#L1", "quote": "（示意）"}]}
  ],
  "relations": []
}
```
