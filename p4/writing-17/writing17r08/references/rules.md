# 规则与模式说明（references/rules.md）

本文件定义 Skill 运行时 Agent 必须遵守的草稿 JSON 模式、节点/关系分类与证据规范。
它随 SKILL.md 一起安装，供 Agent 与复核人引用。**草稿只含从材料中提取的内容，
不允许写入材料中不存在的概念、数值或连接。**

## 1. 草稿 JSON 模式（/app/work/draft.json）

```json
{
  "case_id": "来自 case.json 或 task.md，缺省 unknown",
  "title": "思维导图标题（通常为研究主题/任务主题）",
  "status": "ok | partial | insufficient_evidence | input_error（可选，缺省由脚本推断）",
  "root": {
    "label": "根节点（研究主题/任务目标）",
    "kind": "concept | question | method | evidence | conclusion | task | risk | gap | limitation | decision | milestone | fact | unknown",
    "evidence": [{"locator": "materials/xxx.pdf#p3", "quote": "逐字片段", "provenance": "fact"}],
    "children": [ { "label": "...", "kind": "...", "evidence": [...], "children": [] } ]
  },
  "relations": [
    {"from": "节点标签A", "to": "节点标签B", "relation": "causal",
     "evidence": [{"locator": "...", "quote": "...", "provenance": "..."}], "note": "可选"}
  ],
  "sources": [{"file": "materials/xxx.pdf", "type": "pdf", "pages": null}],
  "uncertainty": [{"item": "节点/关系", "detail": "为何不确定、缺什么证据"}],
  "pending_human": ["需要人工确认的事项"],
  "limitations": ["本轮的已知局限"],
  "contract": { "原 output_contract 字段": "回填值" }
}
```

- `contract` 只回填**需要人工指定**的输出契约字段（如 `deliverable`、`summary`、
  `version_boundary` 等）。`evidence_map`、`sources`、`uncertainty`、`change_log`、
  `case_id`、`status`、`artifacts`、`runtime` 由脚本从树结构自动计算，无需重复填写，
  写了也会被脚本保留的自动值覆盖。
- `evidence.provenance` 三值：`fact`（材料逐字事实）、`summary`（对材料多段的概括）、
  `inference`（基于材料的推断）。禁止把推断标成事实。
- `relations` 是跨层级关系；父子结构本身承载层级，不必为每条父子都写 relation。

## 2. 节点分类（kind）

| kind | 含义 | 示例 |
|-|-|-|
| concept | 核心概念/主题 | 术语、研究主题 |
| question | 研究问题/目标 | RQ、假设 |
| method | 方法/手段 | 实验设计、算法、数据集 |
| evidence | 证据/数据 | 数字、图表、实验事实 |
| conclusion | 结论/发现 | 主结论、贡献 |
| task | 任务/行动 | 待办、下一步 |
| risk | 风险 | 失败模式、不确定性来源 |
| gap | 研究空白 | 未解决问题 |
| limitation | 局限 | 方法/结论边界 |
| decision | 决策/判定 | 取舍、判定标准 |
| milestone | 里程碑 | 阶段目标 |
| fact | 一般事实 | 可归入上述更精确类时优先用精确类 |

## 3. 关系分类（relation）

| relation | 含义 | 方向示例 |
|-|-|-|
| causal | 导致/因果 | A → B：A 导致 B |
| dependency | 依赖/需要 | A → B：B 依赖 A |
| contrast | 对比/对照 | A 与 B 对比 |
| temporal | 时间先后 | A → B：A 在 B 之前 |
| citation | 引用/依据 | 节点 → 引用文献 |
| support | 支持 | 证据支持结论 |
| conflict | 冲突/矛盾 | 两来源不一致 |

无材料依据的连接不得写入；确实存在分歧时用 `conflict` 并同时保留两条来源。

## 4. 证据定位（locator）

- 优先精确到段落/页码：`materials/paper.md#p3`、`materials/paper.pdf#p4`、
  `materials/data.json#$.results[0]`、`task.md#L12`。
- `quote` 必须是与节点逐字对应的原文片段；概括节点可给「依据段落」并标 `summary`。
- 同一指标两处不一致：两条都记录，relation 标 `conflict`，uncertainty 里写明。

## 5. 降级与诚实性规则

- 空输入/无材料：status=`insufficient_evidence`，`limitations` 写明缺什么，不编章节。
- 材料不完整：能建多少建多少，缺失节点不补造，写入 `pending_human`。
- 软件缺失（如无 pdftotext、无 SVG→PNG 转换器）：在 `degradations` 记录，产物仍交付。
- 节点规模爆炸：限制层级 ≤ 6 层、节点 ≤ 120 个；超限时按重要性归并，并在
  `degradations` 说明归并依据。
- 重复运行：脚本无随机性，同一 draft 产出完全一致。

## 6. 产物清单（写入 /app/output/）

| 文件 | 性质 |
|-|-|
| mindmap.md | 可编辑大纲，含证据映射与不确定性小节 |
| mindmap.json | 规范化结构（节点/关系/证据），机器可读 |
| mindmap.mm | FreeMind XML，可导入 FreeMind/XMind 继续编辑 |
| mindmap.svg | 矢量预览，浏览器可打开 |
| mindmap.png | 若环境有光栅化工具则生成，否则省略并在 degradations 说明 |
| result.json | case_id + 原 output_contract 字段 + status/sources/uncertainty/change_log |
