# 导图抽取与溯源规范（references）

本文是 `SKILL.md` 的补充规范，规定 `map_spec.json` 的字段、抽取规则、证据分类与自检清单。
脚本只做确定性渲染与校验；**语义抽取由 Agent 按本规范完成**。

## 1. map_spec.json 结构

```json
{
  "case_id": "案例标识",
  "title": "导图标题",
  "meta": {"purpose": "用途", "audience": "受众", "scope": "材料范围"},
  "root": {
    "id": "n1", "text": "根节点文本", "kind": "root",
    "evidence": {"locator": "materials/paper.pdf#p3", "quote": "原文逐字片段", "class": "fact"},
    "children": [ { "id": "n2", "text": "...", "kind": "concept", "evidence": null, "children": [] } ]
  },
  "relations": [
    {"from": "n1", "to": "n2", "type": "supports", "label": "提供证据", "evidence": {"locator": "...", "quote": "...", "class": "inference"}}
  ],
  "limitations": ["..."],
  "pending_human": ["..."]
}
```

约束：`id` 全谱唯一；`children` 表达层级（分类/组成）；`relations` 表达跨节点的显式关系；
`evidence.locator` 必须指向真实材料位置，`quote` 为材料中的逐字片段（不超过必要长度）。

## 2. 节点 kind（角色标签，决定"抓得准"）

| kind | 含义 | 典型场景 |
|-|-|-|
| root | 中心主题/研究问题 | 必选根节点 |
| concept | 核心概念/术语 | 论文、知识体系 |
| question | 研究问题/子问题 | 论文、实验方案 |
| method | 方法/工具/变量/步骤 | 各场景 |
| evidence | 数据/结果/图表/事实 | 各场景 |
| conclusion | 结论/发现 | 各场景 |
| task | 任务/待办 | 会议纪要、项目计划 |
| goal | 目标 | 项目计划、实验方案 |
| milestone | 里程碑 | 项目计划 |
| deliverable | 交付物 | 项目计划 |
| owner | 负责人 | 会议纪要、项目计划 |
| deadline | 截止时间 | 会议纪要、项目计划 |
| risk | 风险/局限/不确定性 | 各场景 |
| gap | 研究空白 | 论文、综述 |
| decision | 决策/结论 | 会议纪要 |
| other | 其他 | 兜底，应极少出现 |

规则：一个节点一个角色；不要用装饰词当节点；同级节点粒度一致，避免"分类交叉"。

## 3. 关系 type（决定"连得对"）

| type | 含义 | 方向语义 |
|-|-|-|
| supports | 支持/论证 | from 论证 → to 结论 |
| causes | 因果 | from 原因 → to 结果 |
| depends_on | 依赖 | from 依赖方 → to 被依赖方 |
| contrasts | 对比 | from → to 对比对象 |
| precedes | 时间先后 | from 先 → to 后 |
| cites | 引用 | from 引文 → to 被引文献 |
| conflicts | 冲突/矛盾 | from → to 冲突对象 |
| part_of | 组成 | from 部分 → to 整体 |
| example_of | 例证 | from 例子 → to 概念 |
| defined_by | 定义 | from 概念 → to 定义项 |
| measures | 测量/操作化 | from 方法 → to 被测量变量 |
| limits | 限制 | from 限制因素 → to 被限制对象 |

规则：只保留材料有依据的连接；方向必须正确；无依据的连接一律删除或标为 `pending_human`。

## 4. 证据分类（决定"证据可追溯"）

| class | 含义 | 要求 |
|-|-|-|
| fact | 事实/数值/直接陈述 | 有逐字 quote + locator |
| summary | 概括/复述 | 有 locator，quote 为概括依据（非逐字） |
| inference | 推断/组织结论 | 有 locator 指向依据，并注明是推断 |
| unattributed | 未挂接 | 仅允许用于纯结构节点（如"导图中心"），关键节点不得出现 |

冲突信息：同一指标两个来源不一致时，两个都记录，并在 `uncertainty`/`limitations` 标 `source_conflict`。

## 5. 自检清单（对应评分维度）

- [ ] 目标产物可打开、可读、可继续编辑：`mindmap.mm`（FreeMind）+ `mindmap.svg`（预览）+ `outline.md`
- [ ] 节点忠于材料、无材料外事实（实际效果·正确性）
- [ ] 关键概念/问题/方法/证据/结论/任务/风险均有对应节点（实际效果·完整性）
- [ ] 每个关键节点与关系有 locator+quote，且 quote 可在材料中找回（实际效果·可核验性）
- [ ] 关系方向正确，无无依据连接（实际效果·正确性）
- [ ] 同级粒度一致、无重复节点、无分类交叉（使用增益·质量）
- [ ] 脚本一次调用完成从 spec 到四类产物的渲染，减少手工整理（使用增益·效率）
- [ ] 重复运行结果一致（固定布局、顺序由谱决定）（可靠体验·稳定性）
- [ ] 空输入/坏 JSON/缺材料不崩溃，返回 `insufficient_evidence` 或 `input_error`（可靠体验·异常处理）
- [ ] 文档齐全：SKILL.md 说明用途、契约、流程、限制、自查（生态贡献·文档完整性）

## 6. 降级规则（不允许编造）

- 缺数据 → `status=insufficient_evidence`，在 `limitations`/`pending_human` 写明缺口。
- 缺软件 → 写明 `limitations`，改用已有能力；不声称存在未安装的软件状态。
- 节点规模爆炸 → `build_map.py` 截断到 400 节点并在 `degradations` 记录。
- 信息自相矛盾 → 双侧记录 + `source_conflict`，不擅自取舍。
- 任何推断必须标 `inference`，不得伪装成 `fact`。
