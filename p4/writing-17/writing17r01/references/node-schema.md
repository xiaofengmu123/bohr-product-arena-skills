# 节点 / 关系 / 证据 schema（供 Agent 填写 mindmap_spec.json）

## 1. mindmap_spec.json 顶层

```json
{
  "case_id": "case 标识",
  "title": "导图标题（根节点主题）",
  "audience": "目标读者",
  "purpose": "导图用途（如：写作准备 / 实验规划 / 综述梳理）",
  "format": "交付格式约束（如：需 SVG+PDF 预览）",
  "root": {"text": "根节点文本", "kind": "topic", "evidence": []},
  "nodes": [ { "节点对象" } ]
}
```

## 2. 节点对象字段

| 字段 | 必填 | 取值 | 说明 |
|-|-|-|-|
| `id` | 是 | 短字符串，唯一 | 供关系与证据回指 |
| `text` | 是 | 字符串 | 忠于材料的节点短语，不用装饰性词语 |
| `kind` | 是 | 枚举 | concept / question / method / evidence / conclusion / task / risk / note / topic |
| `parent` | 否 | 节点 id，默认 "root" | 父子层级 |
| `relation` | 否 | 枚举 | support / causal / dependency / contrast / temporal / citation / conflict / example / null |
| `relation_to` | 否 | 节点 id | 关系指向的节点（无依据时省略，不得臆造连线） |
| `evidence` | 否 | 数组 | 每条为证据对象（见下） |
| `inferred` | 否 | bool | true 表示概括/推断，非材料原话 |

## 3. 证据对象字段

| 字段 | 必填 | 说明 |
|-|-|-|
| `locator` | 是 | 定位符，如 `materials/paper.pdf#p3`、`notes.md#L12`、`case.json#task` |
| `quote` | 是 | 材料中**逐字**片段，不得改写 |
| `type` | 是 | fact（事实）/ summary（概括）/ inference（推断） |

## 4. 关系枚举语义（连得对）

- `support`：证据/数据支持结论；`causal`：因果；`dependency`：依赖/前置；
- `contrast`：对比/对立；`temporal`：时间先后；`citation`：引用出处；
- `conflict`：冲突/矛盾（须记录两个来源，不隐藏）；`example`：举例。
- 材料无依据的连接一律**不写** relation，避免"无中生有的连线"。

## 5. 证据溯源硬规则

1. 每个关键节点、数值、引用必须挂 `evidence`，含 `locator` + 逐字 `quote`。
2. 同一指标两来源不一致时两条都记录，并在 result.json 的 `uncertainty` 标 `source_conflict`。
3. 区分 fact / summary / inference：推断节点标 `inferred=true`，quote 可空但必须写 locator。
4. 缺失写 `not_reported` 或放入 result.json 的 `uncertainty`，**不得补造数值或引用**。

## 6. 产物文件

| 文件 | 格式 | 用途 |
|-|-|-|
| `mindmap.md` | Markdown 大纲 | 可读、可继续编辑 |
| `mindmap.mm` | FreeMind 1.0.1 XML | FreeMind/XMind 可打开、可编辑 |
| `mindmap.svg` | SVG | 确定性布局预览，可再渲染 PDF/PNG |
| `evidence_map.json` | JSON 数组 | 节点/关系 -> locator + quote |
