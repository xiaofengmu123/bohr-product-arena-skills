# 证据溯源与降级规则（evidence-rules）

本文件把 SKILL.md 第 6、7 节的机制落到可直接执行的判定规则上。核心目标：**不编造由机制保证**——凡不能指回材料逐字片段的事实，就不允许成为页面主张。

## 1. 三类主张（claim_kind）

| 类型 | 含义 | 入页条件 | 必须字段 |
|-|-|-|-|
| `fact` | 论文/数据表可直接读到的数字、术语、结果 | 必须带逐字 `quote` | claim + quote + locator + refs |
| `inference` | 从材料推出的判断（如"图3先于图4更符合因果序"） | 必须带 refs；quote 可缺但须给 locator | claim + refs + locator（或 quote） |
| `design_choice` | 页序、取舍、排版等演示设计决策 | 无需 quote，须给 `reason` | claim + role + reason |

`design_choice` 不是材料事实，不得当作数字来源；它只解释"为什么这样组织页面"。

## 2. 四档置信度（confidence）

| 档位 | 含义 | 能否上页面数字 | 不满足时 |
|-|-|-|-|
| `confirmed` | 有逐字 quote 且与原文一致 | 能 | 缺 quote 即报错 |
| `paraphrased` | 改写，但关键数字/符号保留，quote 已记录 | 能 | 数字必须与 quote 逐字一致 |
| `inferred` | 显式推断 | 仅限非数值推断 | 须标 claim_kind=inference |
| `unverified` | 未核验 | 否，只能进 pending_human | 不得进入 evidence_map 作为页面主张 |

规则：页面上的任何数字（样本量、效应量、准确率、p 值、页码、图表编号）只能来自 `confirmed` 或 `paraphrased`。`main.py record evidence` 对 fact-without-quote 与 confidence=confirmed-without-quote 直接拒绝（退出码 2）。

## 3. locator 与 quote 格式

- `locator`：材料内定位，如 `paper.pdf#p7`、`Table 2`、`materials/data.csv#row12`、`Fig 3`。
- `quote`：从原文逐字复制的片段（可含 UTF-8 标点），不得缩写或补字；PDF 提取质量差时保留原样并在 `uncertainty.notes` 说明。
- 一条 evidence 的 `refs` 必须能解析到 `sources` 数组中的 `ref`；`main.py check` 会对此告警。

## 4. 冲突与缺失

- 同一指标两个来源不一致：两处都记，并在 evidence.`reason` 或 uncertainty 标 `source_conflict`，不擅自选一个。
- 材料没写、图中看不清、位图无数据：写 `uncertainty.unresolved` 或 `pending_human`，字段值用 `null`/`not_reported`，不得补值。
- 扫描页 OCR 结果不可靠：quote 前加 `[OCR]` 标记并降 confidence 到 `inferred` 或 `unverified`。

## 5. 降级登记（degradations）

每条降级写进 `uncertainty.degradations`，五要素齐全：

```json
{
  "what": "论文 PDF 正文缺失",
  "evidence": "扫描 /input 未发现 .pdf",
  "attempted": "pdf-text-tool /input/case.pdf",
  "failure": "文件不存在",
  "fallback": "只用 task.md 中可确证材料，其余页标 limitation"
}
```

常见降级情形与处置（缺数据/缺软件必须如实标注，禁止编造）：

| 情形 | 处置 | 结果标注 |
|-|-|-|
| 论文 PDF/正文缺失 | 只用可确证材料，不补段落 | limitation |
| 图缺失或位图无数据 | 引用原图+图号/页码，不重绘不补值 | uncertainty |
| 模板槽位不足 | 映射最近似版式并记偏差 | uncertainty |
| 字体缺失 | font-discovery 选替代并记录 | uncertainty |
| 页面溢出 | 删要点（字号≥16pt）或拆页 | change_log |
| 导出失败 | 交源文件+已成功预览 | status=partial + 失败原因 |
| 缺 python-pptx/soffice | 回退 Markdown/HTML | status=partial，不伪造渲染 |
| 数字/事实不确定 | 不进页面主张 | uncertainty / pending_human |

## 6. 验收判据

交付前逐条满足，否则 status 不得为 complete：

1. `result.json` 顶层含 `case_id` 与 `output_contract` 全部字段（含未知键）。
2. 每条 evidence 通过 `validate_evidence`（无 error）。
3. 页面数字全部来自 confirmed/paraphrased 证据，quote 与原文逐字一致。
4. `sources`、`version_records`、`change_log`、`self_check.json` 可独立复跑复核。
5. 所有缺数据/缺软件情形都在 uncertainty/limitations/pending_human 中有记录。
