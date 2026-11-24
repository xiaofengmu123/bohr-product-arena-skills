# 自查清单与已知失败模式（bilingual-scholarly-translator）

发布/运行前逐条勾选；对应评分维度。脚本自检用 `python3 -B scripts/selftest.py`，
运行时核查用 `python3 scripts/main.py verify`（报告落盘到
`/app/work/verification_report.json`）。

## 1. 产物完整性（实际效果·完整性 / 可核验性）

- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8，可被 `json.load` 读取。
- [ ] `case_id` 与 `/input/case.json` 一致（`case_id`/`id`/`caseId` 任一命中）。
- [ ] 题目 `output_contract` 全部字段原样包含（未声明时用
      `deliverables` / `fact_source_map` / `version_boundary`）。
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log`。
- [ ] `deliverables[].path` 指向 `/app/output/` 下真实存在的可编辑文件；
      Office/图/幻灯片另有真实渲染 PDF/PNG 预览并写进 `deliverables`。

## 2. 术语稳（实际效果·正确性）

- [ ] 每个领域术语、缩写、上下位词在 `work/ledger.json` 的 `terms` 中有条目，
      含 `source`、`variants`、`canonical_target` 与 `locator`。
- [ ] 同一英文词在不同学科译法不同时，用 `discipline`/`note` 显式区分，不混用。
- [ ] 摘要、正文、图表题注、附录全文统一使用 `canonical_target`（或声明的 variant）；
      `verify` 的 `terms` 检查为 0 error。

## 3. 数字实（实际效果·正确性）

- [ ] `ledger.numbers` 覆盖数字、单位、量级、范围、年份、百分数、统计口径
      （p 值、样本量、置信区间），每项含 `raw`、`core`、`unit`、`locator`。
- [ ] 中英数量级差异（万/亿 vs million/billion）显式记录 `magnitude` 并核对换算。
- [ ] 有效数字不因四舍五入/取整改变；`verify` 的 `numbers` 检查为 0 error。

## 4. 对得齐（实际效果·完整性）

- [ ] 译文每个句段前有 `<!-- seg:<id> -->` 锚，`id` 与 `ledger.segments[].id` 一致。
- [ ] 公式、表格、题注、脚注、参考文献条目完整保留在原位；合并/拆分句段已登记
      到 `version_boundary.modifications`。
- [ ] `verify` 的 `alignment` 检查为 0 error。

## 5. 边界清（实际效果·正确性）

- [ ] 否定范围、模糊限制语（hedge）、因果强度、限定条件不增不减；
      `verify` 的 `semantic_boundary` 无 warning。
- [ ] 引用标记、作者—年份、编号、et al.、上标、方括号格式按目标语言习惯保留；
      `verify` 的 `citations` 检查为 0 error。
- [ ] 增译/省译逐条记录在 `version_boundary.modifications` 的 `what`/`why`。

## 6. 证据溯源（可核验性）

- [ ] 每个关键结论/数字/术语/引用在 `fact_source_map` 有 `source`（locator）
      + 逐字 `quote`；推断项 `note` 标注"推断/待核"。
- [ ] 未新增材料外事实；冲突信息两来源都记录并标 `source_conflict`。

## 7. 使用增益 / 创新增益 / 可靠体验

- [ ] 已运行 `extract` 生成 ledger（单一中间表示），未手写术语表。
- [ ] 已运行 `verify` 并保留报告；error/warning 逐条修订或登记。
- [ ] 前 3 分钟已用 `init` 落盘 `status=in_progress` 骨架，之后只做增量 `add`/`set`。
- [ ] 空输入、坏 JSON、坏编码、超长文本、缺软件均有显式 `uncertainty`/`anomalies`
      登记，`status` 诚实为 `partial`/`failed`，不编造。

## 8. 已知失败模式

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文数字与原文对不上 | 未跑 `extract`/`verify` | 跑 `verify`，按 `numbers` 逐条修复 |
| 同一术语多个译法 | 未锁定 `canonical_target` | 在 ledger 填 `canonical_target` 后重跑 `verify` |
| 句段漏译、段落错位 | 未写 `seg:` 锚 | 逐段补锚，`verify` 的 `alignment` 定位 |
| 引用凭空消失或改写 | 未核对 citation | `verify` 报 citation error 后补回原文 |
| 否定/因果强度被改 | 未过边界清清单 | `semantic_boundary` warning 逐条人工确认 |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文并写入 `/app/work/materials/` |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `init`，之后增量落盘 |

## 9. 术语表模板（Agent 手工补充后写入 ledger 或 result.json）

```json
{
  "terms": [
    {"source": "machine learning", "abbr": "ML",
     "variants": ["machine learning", "ML"],
     "canonical_target": "机器学习", "discipline": "computer science",
     "locator": "seg:src1-1"},
    {"source": "tumor microenvironment", "abbr": "TME",
     "variants": ["tumor microenvironment", "TME"],
     "canonical_target": "肿瘤微环境", "discipline": "oncology",
     "locator": "seg:src2-3"}
  ]
}
```

规则：`canonical_target` 为全文唯一译法；`variants` 是允许出现的同义/缩略写法；
同一 source 词条在不同 `discipline` 下允许不同 `canonical_target`，但必须显式写
`discipline`，不得隐式混用。
