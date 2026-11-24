# 证据溯源与降级规则（slides-01）

## 1. 证据五要素（每个关键结论/数值/图元都必须能回溯）

| 要素 | 要求 |
|-|-|
| `source_id` | 材料文件名，如 `paper.pdf`、`notes.md`；不臆造 |
| `locator` | 章节/页码/图表定位，如 `paper.md#p4`、`Fig.2 caption` |
| `quote` | 逐字命中材料的片段，不用转述替代引文 |
| `claim` | 该页要讲的一个主张，与 quote 直接对应 |
| `figure_refs` | 页内引用的图表编号，如 `Fig. 2`、`Table 1`，必须真实存在于材料 |

规则：quote 必须能在材料文件中逐字命中（`scripts/pptx_util.py` 的 `quote_hits`
可核对）。找不到原文时写 `not_reported`，**绝不补造数值或改写单位**。

## 2. 冲突与不确定性处理

- 同一指标两个来源不一致：两个都记录，并标 `source_conflict`，不隐藏、不选边。
- 材料缺失/读不出/图表无法提取/公式为图片：写入 `uncertainty` 或 `pending_human`，
  在页级 evidence 里标 `not_reported`，不把示意图当实验数据。
- 无法判断的事实：写 `uncertainty`，不写进 `claim` 的正文字面。

## 3. 降级决策表（诚实标注，不编造）

| 场景 | 处置 | 状态 |
|-|-|-|
| `/input/case.json` 缺失或坏 JSON | 写骨架，`case_id="unknown-case"`，原因入 `errors` | `input_error` |
| 有契约但无材料 | 骨架 + 缺口清单入 `pending_human` | `insufficient_evidence` |
| 缺 python-pptx / LibreOffice | 用标准库校验已有 pptx；无 pptx 则 SVG+PNG 并写 `exceptions` | `partial` |
| PDF 加密 / 无法解析 | 记录 `degradations`，用 OCR/渲染工具再试；仍不行标 `not_parsed` | `partial` |
| 模板占位符不足 | 不硬塞；少页或标 `layout_missing`，写 `exceptions` | `partial` |
| 字体缺失 | 优先用模板内嵌/系统字体，记录实际字体于 `runtime` | `partial` |
| 页数超限 | 合并支撑性细节，主线页保留；说明取舍入 `change_log` | `partial` |

## 4. 禁止事项

- 禁止「新建空白演示 + 贴文本框」伪继承模板；必须保留母版/版式/主题。
- 禁止把段落顺序当主线；必须先有 narrative 骨架再定页。
- 禁止讲解备注复读页面文字；备注要写过渡、强调点与被问到的补充证据。
- 禁止占位空文件、预写答案、伪造基准分数或运行结果。
- 脚本不替 Agent 组织内容：每页 claim/evidence/取舍必须由 Agent 真实阅读材料后
  写进 `work/plan.json`。
