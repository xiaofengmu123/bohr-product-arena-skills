# 思维导图 Skill：节点 / 关系 / 证据 Schema

本文件定义 `mindmap_draft.json`（工作草稿）与 `mindmap.json`（规范产物）的结构。脚本 `scripts/mindmap.py render` 负责校验与规范化。

## 1. 草稿结构（Agent 手写）

```json
{
  "title": "主题（默认取自任务/材料）",
  "root": {"id": "root", "label": "根节点", "type": "theme", "detail": ""},
  "nodes": [
    {
      "id": "n1",
      "parent": "root",
      "label": "研究问题：…",
      "type": "research_question",
      "detail": "一句话补充（可选）",
      "evidence": [
        {"locator": "materials/paper.md#L12-14", "quote": "原文逐字片段", "kind": "fact", "confidence": "high"}
      ]
    }
  ],
  "edges": [
    {
      "id": "e1",
      "from": "n1",
      "to": "n2",
      "relation": "causal",
      "label": "导致",
      "evidence": [{"locator": "materials/paper.md#L30", "quote": "…", "kind": "inference", "confidence": "medium"}]
    }
  ],
  "uncertainty": ["不确定性说明…"],
  "pending_human": ["需人工确认…"]
}
```

## 2. 节点 type（层级节点）

根节点二选一：在 `root` 对象给出（`nodes` 里只写子节点，`parent` 指向 `root` 的 id）；或把根节点作为 `nodes` 首项、`parent` 为空，此时 `root` 可省略。不要两处都写同一 id。

| type | 含义 | 提示 |
|---|---|---|
| theme | 根节点/总主题 | 只能有一个根 |
| concept / definition / background | 核心概念、定义、背景 | 避免装饰性词语 |
| research_question / goal | 研究问题、目标 | 可写问句 |
| method / variable / step / control / criterion | 方法、变量、步骤、对照、判定标准 | 实验类方向 |
| evidence / result / conclusion | 证据、结果、结论 | 必须带证据 |
| limitation / research_gap / risk / conflict | 局限、空白、风险、冲突 | 材料中要有依据 |
| task / milestone / deliverable / owner / deadline / issue / decision / action | 任务、里程碑、交付物、负责人、截止、议题、决策、行动 | 会议/项目方向 |
| unknown | 未定/待确认 | 需进 pending_human |

## 3. relation（跨分支关系，表达“连得对”）

`causal`（因果）、`dependency`（依赖）、`compare`（对比）、`temporal`（时间）、`citation`（引用）、`support`（支持）、`conflict`（冲突）、`sequence`（顺序）、`example`（例证）、`part_of`（组成）、`attribute`（属性）。

- 层级父子关系用节点 `parent` 字段表达，不用 edge。
- 关系必须**方向正确**（from → to），且每条 edge 都应带证据；材料中无依据的连接不得出现。

## 4. evidence（证据可追溯）

- `locator`：`文件#段落/页码/行号`，如 `materials/paper.pdf#p3`、`materials/notes.md#L12`。
- `quote`：材料中的**逐字片段**，不得改写、不得补造。
- `kind`：`fact`（事实）/ `summary`（概括）/ `inference`（推断）。概括与推断要降 confidence。
- `confidence`：`high` / `medium` / `low`。

同一指标两个来源不一致时，两条都记录，并在 `uncertainty` 中标注 `source_conflict`。

## 5. result.json 输出契约

`scripts/mindmap.py init` 会：
1. 从 `/input/case.json` 的 `output_contract`（或 `contract` / `result_schema` / `output` / `schema`）读取契约；
2. **原样回填全部键**，缺值填 `null`（列表类键填 `[]`），不改名、不删未知键；
3. 再叠加本题固定字段：

| 字段 | 说明 |
|---|---|
| `case_id` | 输入案例标识（来自 case.json，缺失时降级） |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `root` / `nodes` / `edges` | 思维导图核心结构 |
| `evidence_map` | 扁平证据表（每项带 target 节点/边 + locator + quote） |
| `artifacts` | 可编辑产物路径：`mindmap.json` / `mindmap.md` / `mindmap.svg` |
| `sources` | 输入材料清单 |
| `uncertainty` | 不确定性、冲突、缺口 |
| `pending_human` | 需人工确认项 |
| `errors` / `degradations` | 异常与降级记录 |
| `change_log` | 每次修改记录（含时间戳） |
| `runtime` | 起始时间、耗时、请求数（由平台补真实值，Skill 不编造） |

## 6. 产物格式

- `mindmap.json`：机器可编辑的规范结构（与 result.json 中 nodes/edges 一致）。
- `mindmap.md`：人类可编辑的 Markdown 大纲（层级 + 关系 + 证据内联）。
- `mindmap.svg`：确定性树形布局的可视化预览，同时可文本编辑；可在浏览器/Office 直接打开。
