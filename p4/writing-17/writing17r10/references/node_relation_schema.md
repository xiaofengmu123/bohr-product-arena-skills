# 节点与关系定义（sci-mindmap 数据契约）

本文件是 `mindmap.json` 的规范，Agent 与 `scripts/render_mindmap.py` 共同遵守。渲染器对未知 kind/type 只告警不崩溃，但评分按规范归类。

## 1. mindmap.json 结构

```json
{
  "schema_version": "1.0",
  "title": "导图主题",
  "root": { "id": "n0", "label": "根", "kind": "root", "note": "", "evidence": [], "children": [] },
  "relations": [
    { "id": "r1", "from": "n1", "to": "n2", "type": "support",
      "label": "支持", "evidence": [ { "locator": "materials/paper.md#p3", "quote": "…逐字…", "claim_type": "fact" } ] }
  ],
  "metadata": { "audience": "…", "genre": "…", "task_summary": "…", "materials": ["…"], "pruning": "…" }
}
```

- `root` 为唯一根；`children` 递归嵌套；`id` 全图唯一（缺省时渲染器自动补 `n#` 并告警）。
- `relations` 是树之外的**横向关系**；树内父子关系由 `children` 表达。

## 2. 节点 kind（按抽取目标归类）

| kind | 含义 | 典型抽取对象 |
|-|-|-|
| `root` | 主题/根 | 案例任务主题、论文标题级主题 |
| `research_question` | 研究问题/目标 | 论文 RQ、方案目标、会议议题 |
| `concept` | 核心概念/术语 | 关键定义、对象、变量、实体 |
| `method` | 方法/步骤 | 实验设计、流程、技术路线 |
| `evidence` | 证据/数据/数值 | 结果、图表、测量值、引用出处 |
| `conclusion` / `finding` | 结论/发现 | 论文结论、实验判定、决策结论 |
| `task` | 任务/行动项 | 待办、交付物、里程碑动作 |
| `risk` | 风险/问题 | 风险、局限、反例、失败模式 |
| `gap` / `limitation` | 研究空白/局限 | 未解决问题、材料缺口 |
| `decision` | 决策 | 会议决议、方案取舍 |
| `stakeholder` | 相关方 | 负责人、对象、受众 |
| `milestone` | 里程碑 | 时间节点、交付节点 |
| `other` | 其他 | 兜底 |

**抓得准规则**：不把装饰性词、过渡句、背景套话当成节点；一个节点一个核心语义；同级粒度一致。

## 3. 关系 type（连得对规则）

| type | 含义 | 方向示例 |
|-|-|-|
| `causal` | 因果 | A 导致 B（A→B） |
| `dependency` | 依赖/前提 | B 依赖 A（A→B 表 B 依赖 A） |
| `contrast` | 对比/差异 | A 对比 B |
| `temporal` | 时间/先后 | A 先于 B |
| `citation` | 引用 | A 引用文献 B |
| `support` | 支持/证据支撑 | 证据 A 支持结论 B |
| `conflict` | 冲突/矛盾 | A 与 B 冲突 |
| `part_of` | 组成 | A 是 B 的组成部分 |
| `example_of` | 例证 | A 是 B 的例子 |

**方向必须正确**：依赖/因果/支持等有方向性，写反会扣"结果正确性"。**只写材料有依据的连线**：无依据连线直接判为材料中不存在的连接。

## 4. 证据 claim_type（可追溯规则）

| claim_type | 含义 |
|-|-|
| `fact` | 材料原文直接陈述的事实/数值 |
| `summary` | 对材料多处的概括，附引用段落 |
| `inference` | 推断，材料未明说，需人工确认 |

- `locator` 用"相对 `/input` 的路径 + `#` + 页码/段落"，如 `materials/paper.pdf#p3` 或 `case.json#task`。
- `quote` 必须逐字引用；`summary`/`inference` 的 quote 可写概括句，但 locator 仍须指向依据。
- 冲突信息不隐藏：两个来源不一致时，两条证据都记，并在 `metadata` 或 uncertainty 标 `source_conflict`。
- 不虚构 DOI/引用/数值：缺失写 `not_reported`，需人工确认写 `pending_human`。

## 5. 布局与可读性约定

- 根在左，子节点向右；同级纵向铺开，坐标由叶节点 DFS 槽位确定性计算。
- 标签超过约 18 字宽自动换行；层级建议 ≤5 层，单层 >40 节点时应裁剪/拆分子图并在 metadata.pruning 记录。
- kind 映射固定配色；关系按 type 使用线型与颜色（实线=强关系，虚线=对比/冲突，点线=时间/引用）。

## 6. 最小可复现示例（示意，非正式案例）

```json
{
  "schema_version": "1.0",
  "title": "示例：某方法研究",
  "root": {
    "id": "n0", "label": "某方法研究", "kind": "root", "children": [
      { "id": "n1", "label": "研究问题：能否降低误差", "kind": "research_question",
        "evidence": [ {"locator": "materials/paper.pdf#p1", "quote": "旨在降低误差", "claim_type": "fact"} ],
        "children": [] },
      { "id": "n2", "label": "方法：分步优化", "kind": "method", "evidence": [], "children": [] }
    ]
  },
  "relations": [
    { "id": "r1", "from": "n2", "to": "n1", "type": "support", "label": "支撑",
      "evidence": [ {"locator": "materials/paper.pdf#p3", "quote": "该方法支撑了问题", "claim_type": "summary"} ] }
  ],
  "metadata": { "audience": "评审", "task_summary": "梳理方法与问题关系", "materials": ["materials/paper.pdf"] }
}
```

> 本示例仅用于说明接口与字段，不冒充正式案例结果。
