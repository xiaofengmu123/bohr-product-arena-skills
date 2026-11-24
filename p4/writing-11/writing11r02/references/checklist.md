# 自查清单与已知失败模式

发布/运行前逐条勾选；每一条都对应评分维度或 result.json 的可复核性。

## 产物完整性

- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 含 `case_id`，且与 `/input/case.json` 一致。
- [ ] 含题目 `output_contract` 的全部字段（未声明时为 `deliverables` / `fact_source_map` / `version_boundary`）。
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `change_log` / `runtime` / `degradations`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览并写入 `deliverables`。
- [ ] 运行前 3 分钟已用 `scripts/main.py init` 落盘骨架，`change_log` 首条为 `init`。

## 事实与来源映射

- [ ] 每条术语、数字、单位、引用、图表编号、关键论断都有 `locator` + 逐字 `quote`。
- [ ] 未新增材料外事实；推断与原文明确区分（`note` 标注"推断/待核"）。
- [ ] 存疑引用、找不到原文、结论与文献不符均已登记 `uncertainty` 或 `pending_human`。

## 版本与边界

- [ ] `modifications` 每项含 `what` / `why` / `before_after`（差异）。
- [ ] 未确认项进 `human_confirm`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `editable` 为 true 时，工件是可编辑文本/Office，而非只读截图。

## 一致性

- [ ] 已运行 `scripts/check.py` 并保存 `/app/work/check_report.json`。
- [ ] `errors`（SRC/TRG 段不对齐、重复段 id）已全部修复或交人工确认。
- [ ] `warnings`（数字/引用候选缺失、术语变体、编号跳号）已处理或登记。

## 已知失败模式（对号入座处置）

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文堆成顺文字、无句段对照 | 跳过对齐格式 | 按第 7 节格式写 SRC/TRG 对齐块 |
| 同一术语多种译法、编号跳号 | 未跑一致性检查 | 跑 `check.py`，按报告修订 |
| 改一处引发多处脱节 | 未维护术语/编号索引 | 用报告中的文件:行号定位受影响句段 |
| 数字四舍五入/单位错配 | 只凭印象改写 | 保留原值原格式，万/亿↔million/billion 只换表达不改值 |
| 引用被删或作者归属丢失 | 未核对引用 | 保留标记与参考文献条目，标存疑引用 |
| 结果无法复核 | 未写 `fact_source_map` | 每条论断补 `locator` + `quote` |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `scripts/main.py init` |

## 交付前在非作者环境

```bash
python3 scripts/selftest.py   # 退出码 0
```

空目录、坏 JSON、缺输入三种情况应各自跑一次：`brief`/`init` 不崩溃，产出合法 result.json。
