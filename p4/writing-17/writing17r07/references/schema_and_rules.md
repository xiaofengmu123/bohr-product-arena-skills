# 思维导图字段级契约与抽取规范

> 本文件是 `map_spec.json`（工作稿）与最终产物的内部规范；平台契约字段名以
> `/input/case.json` 的 `output_contract` 为准，脚本自动发现并原样回填。

## 1. map_spec.json 结构

```json
{
  "title": "导图标题",
  "case_id": "…",
  "meta": {"purpose": "用途", "audience": "读者", "scope": "范围"},
  "root": {
    "id": "n1", "text": "根主题", "kind": "root",
    "evidence": {"locator": "", "quote": "", "class": "summary"},
    "children": [ { "id": "n2", "text": "子节点", "kind": "concept",
                   "evidence": {"locator": "materials/x.md#para.3",
                                "quote": "逐字原文", "class": "fact"},
                   "children": [] } ]
  },
  "relations": [
    {"from": "n2", "to": "n3", "type": "causes", "label": "导致",
     "evidence": {"locator": "…", "quote": "…", "class": "inference"}}
  ],
  "limitations": [], "pending_human": []
}
```

规则：
- 根节点 `level=0`，仅一个；父子关系用嵌套表达，不另写 `part_of` 关系边。
- 节点 `id` 唯一（可 `n1..nN`）；脚本会自动去重/补 id。
- `text` 是节点显示文本，来自材料关键信息，不用装饰性词语。
- `evidence` 每个关键节点必填：`locator`（定位）+ `quote`（逐字）+ `class`。

## 2. 节点 kind 枚举（抓得准）

| kind | 含义 | 说明 |
|---|---|---|
| `concept` | 核心概念/术语 | 材料定义或反复出现的主题词 |
| `question` | 研究问题 | 材料明确提出的问题 |
| `method` | 方法 | 实验/分析/统计方法 |
| `evidence` | 证据/数据 | 数字、结果、观测 |
| `conclusion` | 结论 | 材料的结论/论断 |
| `task` | 任务/待办 | 需要执行的行动 |
| `risk` | 风险/局限 | 材料指出的风险或局限 |
| `gap` | 空白/缺口 | 未解决、未来工作 |
| `decision` | 决策 | 会议结论/选择 |
| `goal` | 目标 | 项目/实验目标 |
| `milestone` | 里程碑 | 阶段节点 |
| `deliverable` | 交付物 | 产出物 |
| `owner` | 负责人 | 责任人 |
| `deadline` | 截止 | 时间点 |
| `other` | 其他 | 兜底，尽量少用 |

## 3. 关系 type 枚举（连得对）

| type | 方向含义 | 适用 |
|---|---|---|
| `causes` | from 导致 to | 因果 |
| `depends_on` | from 依赖 to | 依赖/前提 |
| `contrasts` | from 与 to 对比 | 对比/对照 |
| `precedes` | from 先于 to | 时间顺序 |
| `cites` | from 引用 to | 引用 |
| `supports` | from 支持 to | 证据支持结论 |
| `conflicts` | from 与 to 冲突 | 矛盾/冲突 |
| `part_of` | from 属于 to | 组成（仅跨分支时用） |
| `example_of` | from 是 to 的例 | 举例 |
| `defined_by` | from 由 to 定义 | 定义 |
| `measures` | from 测量 to | 方法测量指标 |
| `limits` | from 限制 to | 局限/约束 |

关系必须有材料依据；方向必须正确（`supports` 只能是证据→结论，不能反向）。
无依据的连接不写；确实需要但证据不足的写 `pending_human`。

## 4. 证据 class 三分（可追溯）

| class | 含义 | 要求 |
|---|---|---|
| `fact` | 材料原句事实/数字 | quote 必须能逐字命中原文 |
| `summary` | 对多处的概括 | locator 给出主要出处，quote 摘代表性原句 |
| `inference` | 由材料推断 | 必须标注，不得伪装成 fact |

`unattributed`（未标注）在自检中视为缺证据，需补齐或显式说明。

## 5. locator 约定

| 对象 | locator 示例 |
|---|---|
| Markdown/文本段落 | `materials/paper.md#para.3` |
| 表格/数据 | `materials/table.csv#row.4`、`T1:r3c2` |
| 图/图注 | `materials/fig.png`、`p3#caption1` |
| PDF | `materials/paper.pdf#p3`（先 pdf-text-tool/pdftotext 提取） |
| 会议记录 | `materials/notes.md#L12-L14` |
| 任务说明 | `task.md#para.2` |

每个关键节点与关系都要能按 `locator + quote` 回到原文具体位置；`quote` 必须逐字复制，
脚本 `validate.py` 会核验节点证据覆盖，`build_map.py` 会把证据写入 `evidence.json`。

## 6. 输出回填规则

- `result.json` 顶层必须含 `case_id` 与 `output_contract` 的**全部键**，缺值 `null`。
- `mindmap`/`map` 字段回填完整导图对象（title/root_id/nodes/relations/counts）。
- `evidence_map`/`evidence` 回填节点证据列表；`note` 回填人类可读说明。
- 未知契约键不删除、不改名；无法赋值的写 `null` 并在 `degradations` 留痕。
- 主产物为可编辑 `.mm` + `.svg`，`evidence.json` 与 `outline.md` 供人类复核。
