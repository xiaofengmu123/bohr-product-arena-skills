# 语义保持与最小编辑规则

本文件是 Skill 的硬性规范，Agent 在改写前后都要对照执行；确定性脚本
`scripts/invariants.py` 负责对硬不变量做机器校验，本条规则说明“哪些不能改、
强度如何分级、冲突如何处理”。

## 1. 不变量分类

### 1.1 硬不变量（改写后必须逐字保留，允许仅改空白/全半角）

- `number`：数值（含小数、正负号、科学计数法）。
- `unit`：数值+单位（如 `3.5 mmHg`、`128 mg/dL`、`n = 128`）。
- `citation`：引用编号 `[12]`、`[1,3-5]` 或 `(Smith et al., 2020)`。
- `figure_ref` / `table_ref`：`Fig. 2`、`Table 1`、`图 2`、`表 1`。
- `equation`：行内/行间公式原文。
- `proper_name`：人名、机构名、专有名词、试剂名。
- `stat`：统计量（`p = 0.03`、`HR = 1.4`、`95% CI`）。

判定标准：把不变量 token 归一化（去空白、全角转半角、小写）后，必须仍作为
子串出现在对应改句的归一化文本里。缺失即 `error`，必须回退。

### 1.2 逻辑不变量（含义/强度必须守恒，措辞可改）

- `hedge`：限定强度分五档——`absolute > strong > moderate > weak > agnostic`。
  对应关系：证实/confirm → absolute；表明/show/indicate → strong；
  提示/suggest/imply → moderate；初步/preliminary → weak；未知/尚不明确 → agnostic。
  任何一档变化都必须显式处理（回退，或在 `conservation` 里写 `explicit_reason`），
  不得静默加强或削弱。
- `quantifier`：`all / most / some / few / none` 等量词及其否定范围。
- `negation`：否定词覆盖的范围不得移动（“没有观察到下降”不能变成“观察到上升”）。
- `conditional`：条件句的前提与结论关系（if/then、只有当…才…）。
- `causal`：因果（caused by / due to / 导致）与相关（associated with / 与…相关）
  的区别不得互换。
- `terminology`：同一概念全文唯一写法，禁用变体不得回流。

## 2. 最小编辑原则

1. 只改表达，不改事实与强度；无硬语法错误时优先 `noop`。
2. 同一句若只需改一处，不动其余部分；杜绝整段重写。
3. 术语表已收录的写法优先；新概念先登记再使用。
4. 缩写首次出现给全称（全称（缩写）），之后只写缩写。
5. 数字、单位、引用、图表指向出现任何改动都视为 `error`，不是风格问题。

## 3. 冲突与缺口

- 两个来源不一致：全部记录到 `plan.conflicts` 并标 `source_conflict`，不静默选边；
  结果里写 `pending_human`。
- 材料没有的数据/结论：写 `not_reported` 或 `MATERIAL GAP`，禁止编造。
- 无法判断文体/受众：采用最保守最小编辑（只改硬语法错误），并在
  `uncertainty` 里说明。

## 4. 与脚本的对应关系

| 规则 | 脚本校验 |
|-|-|
| 硬不变量逐字保留 | `invariants.verify_unit` 对 `HARD_KINDS` 做 token 包含检查 |
| 限定强度守恒 | `invariants.verify_unit` 比较 `before_class`/`after_class` |
| 术语全篇一致 | `invariants.verify_terminology` 全稿回扫禁用变体 |
| 覆盖与溯源 | `invariants.summarize_report` 检查每个不变量有 locator+quote 且被某 unit 覆盖 |

## 5. 体裁语域速查（“说得像”）

目标文体在 `plan.genre` 里声明；改写只在该语域内做最小编辑，不得跨体裁“润色”。

| 体裁 | 语域要点 | 常见错误 → 修正 |
|-|-|-|
| 期刊论文（journal） | 无人称、被动/主动按期刊惯例、时态分结果（过去）与结论（现在）、限定词克制 | “we strongly believe” → “these results indicate”；口语缩略 → 全称 |
| 学位论文（thesis） | 章节自足、方法可复现、结论不越界、第一人称由院系规范决定 | 摘要与结论照搬引言 → 各章按功能重述，不得新增断言 |
| 基金申请书（grant） | 客观论证“为何做、为何现在、为何是我”，前景句用“有望/预计”而非“必然” | “将彻底解决” → “有望改善”；“全球首创” → 量化相对基线 |
| 审稿回复（review reply） | 先复述审稿意见，再逐点回应；引用改稿行号；语气谦谨但不卑 | “You are wrong” → “We agree that X; revised text reads …” |
| 英文摘要（abstract） | 背景一句、方法一句、关键结果带数据、结论一句带限定词；不出现新信息 | 直译腔（“make a research on”）→ 动词化（“we studied”） |

共同红线：不因“更像某体裁”而强化结论、新增数据或改变引用与图表指向。语域调整
只允许改变措辞与句式，不允许改变主张强度；做不到时回退为最小编辑并在
`uncertainty` 说明。
