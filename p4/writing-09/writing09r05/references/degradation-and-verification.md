# 降级、证据核验与 change_log 规则

本文件规定：材料缺失/冲突/超长、软件不可用、引用无法核实时的诚实处置，以及
`change_log` 的追加语义。核心原则一句话：**缺什么就记什么，不补造**。

## 1. 已完成 / 拟开展 / 无法判断 三态

结果与讨论写作中的事实来源三态（贯穿 `facts_sources` 与正文语气）：

- `completed`：材料中已报告（有图/表/统计输出原文），可写入正文结果段。
- `planned` / `proposed`：仅当 case.json 要求把“拟开展”与“已完成”分开时才启用，
  一般结果与讨论写作不存在拟开展数据。
- `unknown` / `pending_human`：材料缺失、单位不明、误差棒含义不明、p 未报告等，
  正文写“未报告”或标 `pending_human`，不得补造数值或检验结果。

## 2. 降级策略（禁止编造）

| 场景 | 处置 |
|------|------|
| 材料缺失 | 字段留 `null`/`[]`，在 `uncertainty` 或 `version_boundaries` 写“缺失/无法判断” |
| 图表读数不清 | 标 `read_uncertainty` + `pending_human`，正文用“约/approximately”，不给伪精确值 |
| 误差棒含义不明 | `pending_human`，禁止默认按 SD 或 SEM |
| 单位不明/需换算 | 记录换算假设；无法确认时 `pending_human` |
| 引用找不到出处 | 保留位置并标 `unverified`，不伪造 DOI/URL |
| 两个来源冲突 | 都记录，标 `source_conflict`，正文取稳妥口径并说明 |
| 软件/工具不可用（无 pdftotext、office 渲染、latex） | 写 `limitations`，交付纯文本/结构化产物，不假装已渲染 |
| 输入超长/跨度过大 | 截取关键段落，`uncertainty` 写“已截断/未覆盖” |
| 运行失败 | `status=failed`，`change_log` 记录原因与已完成的中间产物，result.json 仍落盘 |
| 空输入目录 | `status=in_progress`（骨架）+ `uncertainty` 记录“无材料”，退出码 0 |

## 3. status 取值

- `in_progress`：骨架或部分填充（前 3 分钟落盘即此状态）。
- `partial`：时间/请求耗尽但有可用初稿；`limitations` 必须说明未完成部分。
- `complete`：`validate` 无结构错误、关键论断有 locator、局限已写。
- `failed`：运行失败或材料不可用导致无法交付；result.json 仍须合法落盘。
- 参考包的 `insufficient_evidence` 语义并入 `partial` + `uncertainty`，保持平台字段简洁。

## 4. change_log 追加语义

- `change_log` 是 append-only 的数组：每次对 `deliverable` 正文、`facts_sources`、
  `version_boundaries` 或 result.json 结构做实质修改，都要 `append_change` 一条。
- 每条必含 `seq / ts / actor / action / target / before / after / reason`；
  `seq` 从 0 递增，时间戳 UTC ISO-8601。
- `target` 写被改对象（如 `deliverable`、`facts_sources[2]`、`result.json`），
  `before/after` 写版本或差异摘要，`reason` 写“为什么改”（对应自检发现的问题）。
- 自检回修必须回写 change_log：第三者可据此判断“某句为什么这么改”。

## 5. 证据核验规则

- 每个关键数字/术语/引用/论断 → locator + 逐字 quote；`quote` 必须是材料原文片段，
  不是转述。
- `kind` 区分 `figure / table / stat_output / reference / author_inference`。
- `status` 区分 `verified / unverified / pending_human / source_conflict`；
  `complete` 前不允许有未经说明的 `unverified` 关键论断。
- 不因文件名或领域标签补造内容：`fig3.png` 没读出来就写“未读/读不清”，不写数值。

## 6. 重复运行与确定性

- 脚本无随机源，同一输入重复运行产出的骨架与摘要一致。
- `change_log` 的时间戳会变化，但结构、字段顺序与编号确定；这是有意的可审计性。
- 输出 JSON 均为 UTF-8、`ensure_ascii=False`、两空格缩进、末尾换行。
