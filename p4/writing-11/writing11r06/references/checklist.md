# 自检清单与已知失败模式（academic-translation-bilingual）

发布/交付前逐条勾选。勾选不替代实际运行证据；凡是"勾了但脚本报 error/warning"的，
必须先在 `result.json` 里修订或登记到 `uncertainty` / `human_confirm`。

## 一、产物完整性

- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8，能被 `json.load` 读取。
- [ ] `case_id` 与 `/input/case.json` 原样一致（缺键时按 `id`/`caseId` 回退）。
- [ ] 题目 `output_contract` 的三个字段全部存在：`deliverables`、`fact_source_map`、`version_boundary`；若 `case.json` 声明了其他键，也原样回填（未知值给 `null`/`[]`，不改名不删键）。
- [ ] 额外字段齐全：`status` / `sources` / `uncertainty` / `pending_human` / `change_log`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图/LaTeX 类工件附真实渲染 PDF/PNG 预览并写入 `deliverables`。
- [ ] 中间产物已落盘：`plan.json`（翻译方案）、`glossary.json`（术语表）、`alignment.json`（句段对齐）、`fact_check.json`（数字/引用核对），均写入 `deliverables` 或 `sources`。

## 二、事实与来源映射

- [ ] 每条数字、单位、量级、百分数、样本量、时间范围、公式符号都有 `source`（文件+页码/段落 locator）。
- [ ] 每条引用标记（作者—年份、编号、et al.、上标、方括号）都在 `fact_source_map` 或 `fact_check.json.citations` 有 `source` 与 `kept` 状态。
- [ ] 每条术语都有 `glossary.json` 条目（src/tgt/domain/locked），同一译法锁定到摘要、正文、图题、附录。
- [ ] 每个关键论断带 `locator` + 逐字 `quote`，不新增材料外事实；推断单独标 `note="推断/待核"`。
- [ ] 原文与译文句段一一对应，`alignment.json` 的 `orphan_src`/`orphan_tgt` 为空或已登记。

## 三、版本与边界

- [ ] `version_boundary.modifications` 每项含 `what`/`why`/`before_after`，记录增译、省译、语序调整的差异与理由。
- [ ] 未确认项全部进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable=true` 时，译文与对照表是可编辑文本/Markdown/LaTeX，而非只读截图。
- [ ] 否定范围、限定条件、因果强度、模糊限制语（hedge）的强度未被改写；不确定处标注。

## 四、一致性（四稳）

- [ ] **术语稳**：已跑 `scripts/selftest.py`；同一英文词在不同学科下译法不同的情况已按 `domain` 分列。
- [ ] **数字实**：万/亿 与 million/billion 换算正确，有效数字、单位、量级、统计口径原样落地。
- [ ] **对得齐**：段落顺序、图表编号、题注、脚注、参考文献条目完整保留原位。
- [ ] **边界清**：et al.、上标、方括号等格式差异按目标语言规范处理，归属与引用完整。

## 五、已知失败模式（对号入座处置）

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文只给一段"建议翻译"而非整篇 | 跳过 P2 分块翻译 | 先跑 `main.py discover` + 建 `plan.json` 分段，再逐段译 |
| 同一术语多种译法 | 未建 glossary.json | 抽术语→锁定译法→全篇替换，冲突记 `conflicts` |
| 数字被四舍五入/单位错配 | 未跑 `extract_numbers` | 用 `fact_check.json.numbers` 逐条对拍，存疑标 `needs_human` |
| 译文丢了图题/脚注/参考文献 | 分块时按"正文"切 | 分段模式保留 figure/table/footnote/reference 位置标签 |
| 引用找不到原文却照抄 | 未核对 | 登记 `uncertainty`，保留原引用，不改写结论 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `main.py skeleton`，之后只增量 `update`/`add-list` |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |

## 六、术语表模板（glossary.json 结构，供 Agent 填写）

```json
{
  "direction": "en->zh",
  "domain": "biology",
  "terms": [
    {"src": "mortality", "tgt": "死亡率", "domain": "epidemiology",
     "locked": true, "note": "全文统一", "occurrences": ["sec1:para3", "fig1:caption"]}
  ],
  "conflicts": [
    {"src": "cell", "tgt_a": "细胞", "tgt_b": "电池", "domain": "electrochemistry",
     "resolution": "按上下文域分列"}
  ]
}
```

规则：`locked=true` 表示译法已锁定，不得再随意变体；`occurrences` 记录出现位置，
便于局部修改时定位受影响段落（文件:行号/段落号）。
