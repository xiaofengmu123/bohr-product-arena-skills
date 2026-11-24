# 评分维度对齐与复现规范（rubric_map）

本文件把题目评分维度逐条映射到执行流程、自检项与降级规则，是 SKILL.md 第 5/7/8
节的展开。目的是让 Agent 在任何一步都能回答“这一步对应哪个评分点、如何才算完成”。

## 1. 维度 → 流程 → 自检 全表

| 评分维度 | 分值 | 考察内容 | 流程步骤 | 自检项 | 完成标准 |
|---|---|---|---|---|---|
| 实际效果·任务完成度 | 15 | 数据真的被清洗到可统计状态，并按题完成描述/推断/效应量/CI/解释 | ⑤⑥ | A1、A3 | analysis.json 含 cleaning/descriptive/inferential/effects/ci/interpretation |
| 实际效果·结果正确性 | 15 | 统计量、df、p、效应量、CI 数值正确；方法与分布/结构匹配；单位与有效数字符合口径 | ⑤⑥ | A4、A5 | selftest 通过；方法与假设检验结论一致；单位写明 |
| 实际效果·结果完整性 | 5 | 覆盖题目统计环节与七类关键字段 | ⑤⑦ | A2、A6 | 七类情形各自有计数/规则；result.json 契约逐键回填 |
| 实际效果·结果可核验性 | 5 | 数据来源、清洗规则、代码、环境、随机种子、中间结果可复跑同一组数字 | ⑦⑧ | A7、A8 | 无随机数；selfcheck 两次运行比对一致；cleaned.csv+core_impl.py 可复算 |
| 使用增益·质量 | 15 | 相比基础 Agent 在统计正确性、假设检验规范性、解释质量上稳定提升 | ⑤ | A4、B1 | 自动选检验、效应量/CI 绑定结论、p 写精确值 |
| 使用增益·效率 | 5 | 减少清洗/重算/核对/撰写的人工量 | ⑤ | B1 | 确定性脚本一键产出全套产物 |
| 使用增益·可验证性 | 5 | 同一数据、同一问题、同一权限下加载/不加载两套原始输出并存 | ⑦ | A7 | 脚本输出中间 JSON，可对照基础 Agent |
| 创新增益·新增能力 | 5 | 基础 Agent 难以稳定实现：按分布/设计自动选检验、单位不明主动标注 | ⑤ | B1、B2 | plan 缺失时 auto_detected=true 并写 uncertainty |
| 创新增益·方法改进 | 5 | 清洗规则/检验流程/效应量选择/解释有实质改进 | ⑤⑥ | A4、A5 | 标准库 Shapiro-Wilk、Brown-Forsythe、Bonferroni/BH |
| 创新增益·创新证据 | 5 | 用真实数据与对照说明创新点位置与价值 | ⑥ | D2 | 如实记录，不虚构对照 |
| 可靠体验·调用易用性 | 3 | 正确时机触发；新数据能跑完并读懂输出 | 全流程 | D1 | SKILL.md 写明用途/边界/调用/示例 |
| 可靠体验·运行稳定性 | 4 | 不同规模/字段类型/缺失比例/重复运行下稳定产出 | ⑤⑧ | C1、C2、A7 | 类型推断+降级表+确定性 |
| 可靠体验·异常处理 | 3 | 样本不足/方差为 0/缺失过高/假设不成立/单位不明/DB 失败 | ⑤⑧ | C2 | 降级策略逐条命中 |
| 生态贡献·文档完整性 | 3 | 数据字典、方法说明、依赖版本、示例、边界、维护齐全 | 本文件 | D1 | 三文件齐备、说明与版本一致 |
| 生态贡献·资源可复用 | 4 | 清洗脚本、评分表、报告模板可被他人直接复用 | ⑤⑦ | D2 | 标准库、相对路径、可复跑 |
| 生态贡献·分享推广 | 3 | 沉淀可分享的统计规范、失败案例、避免误用 | references | D2 | stats_checklist 列出常见误用 |

## 2. result.json 契约细节

- 顶层：`case_id` + `output_contract` 全部字段 + 信封 `status/sources/uncertainty/
  change_log/errors/degradations/pending_human/runtime`。
- 契约回填语义映射：`descriptive/inferential/effects/confidence_intervals/
  interpretation/quality/cleaning/evidence/code/figures`（含下划线/连字符变体与复数）。
  未知键按值类型给 `null` 或 `[]`，嵌套 dict 递归回填。
- `sources[]`：`{source_id, path, kind, rel_path}`，path 为绝对路径、rel_path 为相对 /input。
- `change_log[]`：`{id, action, before, after, reason}`，顺序即步骤顺序（确定性，
  不含墙钟时间；时间戳统一放 runtime.generated_at）。
- `status`：`ok`（全流程完成）、`partial`（部分完成或降级）、`insufficient_evidence`
  （缺材料）、`input_error`（材料损坏无法解析）。

## 3. 复现要求

1. 无随机数：所有统计量纯函数；selftest 用固定 seed 只做实现校验，不进正式产物。
2. 中间结果落盘：`/app/work/analysis.json`、`index.json`、`schema.json`、`plan.json`。
3. 复跑入口：`/app/output/analysis_code.py` 调用 main；`/app/output/core_impl.py`
   为同一实现副本，可在 /app/output 内 `python analysis_code.py` 重跑。
4. 证据溯源：每个关键结论给 `{id, claim, locator, quote, basis}`；locator 形如
   `data.csv#column`，quote 为该数值/规则的逐字串，冲突标 `source_conflict`。

## 4. 降级与诚实原则

- 缺数据 → `insufficient_evidence`；坏输入 → `input_error`；脚本失败 → `partial`。
- 不适用检验：组内方差为 0、n<2、缺失比例过高、假设不成立 → 标“不适用”，不给 p。
- 单位不明 → `unit_unknown`；软件不可用 → 标准库替代并标口径；绝不编造状态或数据。
- 退出码恒 0；异常走 `_land_fallback` 落一份合法 partial result.json。
