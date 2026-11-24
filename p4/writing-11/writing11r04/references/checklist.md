# 自查清单与已知失败模式

发布前逐条打勾；`scripts/selftest.py` 会自动验证结构项，语义项仍需 Agent 逐条确认。

## 产物完整性

- [ ] `/app/output/result.json` 存在、合法 JSON、UTF-8、可被 `json.load` 读取。
- [ ] 含 `case_id`，与 `/input/case.json` 一致。
- [ ] 含题目 `output_contract` 全部字段（未声明时为 `deliverables` / `fact_source_map` / `version_boundary`）。
- [ ] 含 `status` / `sources` / `uncertainty` / `pending_human` / `limitations` / `change_log`。
- [ ] `deliverables[].path` 指向的文件真实存在；Office/图类工件有渲染 PDF/PNG 预览，无渲染工具已在 `limitations` 标注。

## 术语稳

- [ ] `/app/output/glossary.json` 存在：每项含 `canonical` + `variants` + `discipline` + `context`。
- [ ] 同一术语在摘要/正文/图题/附录只用一个译法；同词异学科（如 `cell` 电池/细胞）已按上下文选译并保留两个候选。
- [ ] `check_translation.py` 报告 `glossary_inconsistent == 0`；未命中术语已登记 `uncertainty`。

## 数字实

- [ ] 数字、单位、量级、范围、年份、百分数、样本量原样落地；无四舍五入、无单位错配、无"顺手取整"。
- [ ] `million/billion` 与 `万/亿` 换算正确；有效数字位数不变。
- [ ] 每个关键数字在 `fact_source_map` 有 `source` + `locator` + `quote`；找不到的标 `not_reported`，绝不补造。

## 对得齐

- [ ] 原文与译文句段一一对应；段落顺序、图/表/公式/题注/脚注/参考文献原位保留。
- [ ] 引用标记（`[n]`、作者—年份、et al.、上标）保留或按约定转换，无遗漏。
- [ ] 双语对照稿可直接逐段修改，不破坏结构。

## 边界清

- [ ] 否定范围、因果强度、模糊限制语（not/may/suggest/approximately/significantly 等）逐条核对，语义强度不变。
- [ ] 增译/省译有说明；不写作者没说过的结论；归属完整。
- [ ] `fact_source_map` 中 `status` 诚实：verified 仅限能溯源项，推断标 `inferred`，存疑标 `pending_human`。

## 版本与边界

- [ ] `modifications` 每项含 what / why / before_after；局部修改未扩大波及范围。
- [ ] 未确认项全部进 `human_confirm` 与顶层 `pending_human`；材料缺口进 `material_gaps`；异常进 `anomalies`。
- [ ] `limitations` 显式列出产物边界与证据；`editable=true` 时工件是可编辑文本而非截图。

## 已知失败模式（对号入座处置）

| 现象 | 原因 | 处置 |
|-|-|-|
| 译文堆成流水、术语每次重译 | 未先建术语表 | 先抽术语表再译，锁死译法 |
| 数字被取整/单位错配 | 未做数字核对 | 用 `check_translation.py` 数字队列逐条核 |
| 引用/公式/脚注丢失 | 未跑一致性检查 | 跑 checker，error 全部处理或交人工 |
| 否定/因果范围走样 | 逐字直译或意译过头 | 按 manual-verify 队列核对边界清 |
| 改一处引发多处脱节 | 未维护术语/编号索引 | 用报告 file:行号 定位受影响句段 |
| 缺材料却补造内容 | 未登记缺口 | 登记 uncertainty/limitations，status=partial |
| 中途崩溃全丢 | 未先落盘骨架 | 前 3 分钟 `main.py init` |
| PDF 只凭文件名臆测 | 未真实解析 | 用平台 PDF 工具读正文，不读标签 |
