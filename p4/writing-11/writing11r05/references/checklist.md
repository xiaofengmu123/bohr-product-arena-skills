# 自查清单与已知失败模式

发布/交付前必须逐条打勾。每条都对应评分维度；`scripts/selftest.py` 会把这些
可机器验证的部分跑一遍，人工部分由 Agent 按本清单勾选。

## A. 输出契约完整（对应「可靠体验 / 生态贡献」）

- [ ] `/app/output/result.json` 存在、UTF-8、可被 `json.load` 读取。
- [ ] 顶层必含 `case_id`、`status`、`deliverables`、`fact_source_map`、
      `version_boundary`、`sources`、`uncertainty`、`pending_human`、`change_log`。
- [ ] `case.json` 里若声明了 `output_contract`，其键名**原样回填**，缺值给
      `null`/`[]`，不改名、不删未知键；未声明时用题目默认三字段
      `deliverables` / `fact_source_map` / `version_boundary`。
- [ ] `version_boundary` 含 `modifications`、`human_confirm`、`material_gaps`、
      `anomalies`、`editable`。
- [ ] 每条 `change_log` 含 `time`、`action`、`key`、`detail`，且由脚本自动追加，
      不手写伪造。
- [ ] `status` 取值诚实：`success | partial | failed | insufficient_evidence |
      input_error`（未完成绝不写 `success`）。

## B. 证据溯源（对应「实际效果 / 结果可核验性」）

- [ ] 每个关键数字、术语、引用、图表编号、关键论断在 `fact_source_map` 有记录。
- [ ] 每条记录含 `source`（文件+页码/版本）**以及** `locator` + `quote`（逐字引用）。
- [ ] `quote` 必须是从原始材料中**逐字**取出的片段，不得转写、不得凭记忆补写。
- [ ] 未新增材料外事实；推断与原文明确区分（`note` 标注「推断/待核」）。
- [ ] 存疑引用、找不到原文、结论与文献冲突均已登记 `uncertainty` / `pending_human`。

## C. 翻译四锁（对应「实际效果 / 结果正确性」）

- [ ] **术语锁**：`glossary.json` 每条有 `canonical` 与允许 `variants`；译文全文
      （含图表题注、脚注、附录）只出现一个译法，跨学科一词多译已分列条目。
- [ ] **数字锁**：数字、单位、量级、范围、年份、统计口径原样落地；`3,500,000`、
      `350万`、`3.5 million` 的数值等价关系已通过检查；未被四舍五入或改整数。
- [ ] **结构锁**：原文与译文句段一一对应；公式、表格、题注、脚注、参考文献条目
      原位保留；`alignment.json` 的 `src_id` 连续、每段有 `locator`；保留原位的
      公式/表格段显式 `preserved=true`。
- [ ] **边界锁**：增译/省译、否定范围、因果强度、模糊限制语保持原意；引用标记
      （作者—年份、编号、et al.、上标、方括号）与归属完整，不出现作者没说过的结论。

## D. 使用增益（对应「使用增益」）

- [ ] 已运行 `python3 scripts/main.py ... check` 并保留 `check_report.json`。
- [ ] error（关键数字丢失、契约缺字段、对齐断链）全部处理或交 `pending_human`。
- [ ] warning（术语变体、引用缺失、悬空对齐、数值疑似补写）已处理或登记。
- [ ] `version_boundary.modifications` 每条含 `what` / `why` / `before_after`，
      且只记录真正改动处，未扩大波及范围。
- [ ] 报告给出「文件:行号」或段落 `locator`，无需通读全文即可定位受影响句段。

## E. 可靠体验（对应「可靠体验」）

- [ ] `scripts/selftest.py` 全 PASS（空目录、坏 JSON、正常流程三种情况）。
- [ ] 重复运行同一输入结果一致（脚本确定性、固定种子、无随机改写）。
- [ ] 对材料缺失、引用找不到原文、编号不连续、超长截断、格式不支持均显式处置。

## 已知失败模式（对号入座）

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文堆成顺文字、无句段对照 | 跳过对齐 | 逐段写 `alignment.json`，每段带 `locator` |
| 同一术语多种译法、图表题注不一致 | 未锁术语表 | 先建 `glossary.json`，再全篇替换校验 |
| 数字被「顺手取整」或单位错配 | 未跑数字锁 | 关键数字记 `fact_source_map`，跑 `check` |
| 引用/脚注/参考文献被丢 | 未做引用保留 | 跑 `check` 的引用保真，缺失处登记 |
| 增译出作者没说的结论 | 未守语义边界 | 否定/因果/模糊限制语逐句对照，改动入 `modifications` |
| 结果无法复核 | 未写溯源 | 每条论断补 `locator` + `quote` |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟先 `init`，之后只增量 `add` |

## 术语表模板（glossary.json）

```json
{
  "terms": [
    {"canonical": "结合亲和力", "variants": ["binding affinity", "亲和力"],
     "discipline": "生物化学", "require_canonical": true},
    {"canonical": "均方根误差", "variants": ["RMSE", "均方根差"],
     "discipline": "统计学"}
  ]
}
```

规则：`canonical` 为全文统一译法；`variants` 列出允许出现的同义/缩写；若正文同时
出现多个 variant，`check` 会报 warning，需统一或在 `note` 显式说明。
