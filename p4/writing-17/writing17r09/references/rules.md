# 思维导图 spec 与证据规范

本文件是 `scripts/mindmap.py` 读取的 `spec.json` 规范，也是抽取时的质量规则。脚本只做确定性的格式化与布局，不修改语义内容。

## 1. spec.json 结构

```json
{
  "meta": {"title": "导图标题", "purpose": "用途（论文梳理/实验方案/项目计划/会议纪要/知识体系）", "audience": "目标读者"},
  "nodes": [
    {"id": "n0", "label": "根节点", "parent": null, "kind": "topic",
     "evidence": {"locator": "materials/paper.md#L3", "quote": "原文逐字片段", "source": "materials/paper.md", "kind": "fact"}}
  ],
  "relations": [
    {"from": "n1", "to": "n2", "type": "causal",
     "evidence": {"locator": "materials/paper.md#L5", "quote": "原文逐字片段", "source": "materials/paper.md", "kind": "summary"}}
  ]
}
```

约束：`id` 全库唯一且非空；`parent` 为已存在节点 id 或 null；父边不能成环；`relations` 端点必须已存在；每条关系尽量带 `evidence`。

## 2. 节点 kind 词表

| kind | 含义 | 抽取要点 |
|-|-|-|
| topic | 根/主题 | 材料核心对象，通常唯一根节点 |
| concept | 概念/术语 | 关键术语与定义，非装饰性词汇 |
| question | 研究问题 | 明确的问题、目标、假设 |
| method | 方法 | 方法、变量、步骤、对照 |
| evidence | 证据/数据 | 数字、图表、实验结果、观测 |
| conclusion | 结论 | 结论、发现、判断 |
| task | 任务/待办 | 行动项、里程碑、交付物 |
| risk | 风险/局限 | 局限、空白、风险、冲突 |
| reference | 引用/来源 | 文献、出处 |

同级粒度要一致，避免把「概念」和「结论」混在同一层；重要信息不得埋没在装饰性细节下。

## 3. 关系 type 词表

| type | 含义 | 方向要求 |
|-|-|-|
| causal | 因果 | 方向必须从因指向果 |
| dependency | 依赖 | 后者依赖前者 |
| contrast | 对比/差异 | 双向对立，任选一端为 from |
| temporal | 时间先后 | 从先到后 |
| support | 支持/论证 | 证据指向被支持结论 |
| conflict | 冲突/矛盾 | 标注不一致来源 |
| citation | 引用来源 | 论断指向文献 |

关系不能凭空连接：每条都必须能在材料中找到依据（把依据写进 `evidence`）。

## 4. 证据规范（证据可追溯的硬要求）

- `locator`：`materials/paper.md#L3`（行号）或 `#p3`（段落/页码）或 `#§2`（章节），须能定位回原始文件。
- `quote`：**原文逐字片段**，用 `scripts/main.py` 的 `verified` 字段自动核对：`verbatim`（逐字命中）/`partial`（前缀命中）/`not_found`（未命中）/`no_text`（无文本源）。
- `source`：材料相对路径；与 `locator` 一致时可不重复。
- `kind` 区分：`fact`（材料明示的事实）、`summary`（多句概括）、`inference`（Agent 推断）。推断必须标 `inference`，不得冒充事实。

## 5. 抽取质量规则（抓得准/理得清/连得对）

1. 先读 task.md 确定体裁、受众、格式约束；先读材料原文再抽取，不看文件名标签补造。
2. 根节点 = 材料核心对象；二级节点 = 问题/方法/证据/结论/任务/风险等大维度；三级以下展开。
3. 同一层不交叉分类、不重复节点；合并同义节点而非堆叠。
4. 数字、术语、引用、图表、关键论断必须挂证据；无依据的连接删除。
5. 材料不足、冲突、需人工确认处写入 `version_boundaries` 对应字段，不编造。
