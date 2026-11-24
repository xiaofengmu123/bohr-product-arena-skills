# 中间表示与可复核（本 Skill 的核心设计取舍）

本 Skill 与“直接材料 → 成稿”打法的区别在于：**先落三层结构化中间产物，再据此
成稿，最后用脚本校验**。评审人或第三方可沿着
`manuscript.md → result.json → evidence_map / figure_model / plan` 逐句复核，
无需重读全部原始材料。

## 三层中间表示

| 文件 | 职责 | 回答的问题 | 由谁填充 |
|-|-|-|-|
| `plan.json` | 写作规划：受众/文体/格式、结果/讨论分段、图表清单、claim 槽位、约束 | 分得清 | 脚本搭骨架，Agent 填内容 |
| `figure_model.json` | 图表与数值模型：图/表/数值逐条带 locator + 逐字 quote + 单位 + 统计量 + 读取状态 | 读得准 | 脚本自动提取数值/统计量与表格，Agent 回填图面板 |
| `evidence_map.json` | 论断→证据映射：claim 文本、类型(实测/推断/引用)、强度、状态(verified/unverified) | 核得了 | Agent 填，脚本校验 |

## 数据流

```
原始输入 (/input) --discover--> input_summary.json
   |--skeleton--> result.json (partial, 前 3 分钟落盘)
   |--scaffold--> plan.json / figure_model.json / evidence_map.json
   |--write-----> manuscript.md (每段挂 claim id)
   |--merge-----> result.json 回填 deliverable/facts_sources/version_boundaries
   `--validate--> 结构 + 一致性 + 禁词 + 来源回检
```

`main.py --phase bootstrap` 一次完成 discover + skeleton + scaffold（3 分钟
内必须跑完的“最小诚实交付”）；之后只做增量 `merge_patch`，不重写整份结果。

## 可复核性保障

- 数值来源自动对照：脚本从材料提取 `numeric_inventory`，Agent 写作时把正文
  数值与库存逐条对齐；对不上的数值要么补来源、要么删除。
- 逐字 quote：`facts_sources` 与 `evidence_map` 每条都要求 `locator + quote`；
  只有 locator 没有 quote 会被 `validate_result` 判为问题。
- 一致性回检：`validate_result` 检查 `case_id`、output_contract 全部字段、
  保留字段、`status=complete` 时无 null 契约字段、正文禁词。
- 不隐藏冲突：同一指标两个来源不一致时两条都记录，`type=source_conflict`。

## 与“直接成稿”的取舍

| 维度 | 直接成稿 | 本 Skill 中间表示 |
|-|-|-|
| 出稿速度 | 快 | 多 1–2 次请求（搭骨架） |
| 复核成本 | 重读材料 | 沿 JSON 链逐句可查 |
| 数值一致性 | 靠自觉 | 脚本强制对照 |
| 失败恢复 | 半稿难续 | partial 骨架可增量合并 |
| 换图/换学科 | 需改整篇 | 只换 figure_model 与 claim 槽位 |

因此本 Skill 牺牲少量首次出稿延迟，换取“可独立复核、可增量续写、可跨案例复用”
三项收益，正是本题“完成产物，也保留来源”的要求。
