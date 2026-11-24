# checklist.md — 提交/定稿前逐条核查

每条均可勾选；对应题面评分维度。运行 `python scripts/selftest.py` 可自动验证脚本链（骨架/画像/校验/报告/定稿/确定性/空输入），本文为结论质量的人工核查项。

## A. 输出契约（可靠体验 / 生态贡献）

- [ ] result.json 可被 `json.load` 解析；`case_id` 非空；`status` 合法
- [ ] 含 `case_id` + `output_contract` 全部字段 + `status`/`sources`/`uncertainty`/`change_log`
- [ ] `output_contract` 字段原样回填，缺值 `null`/`[]`，未改名删键
- [ ] `summary` 为 1–3 句平实结论，无无据断言
- [ ] `change_log` 覆盖骨架→画像→结论→定稿；`runtime` 含耗时

## B. 五段结论质量（实际效果：任务完成度 / 正确性 / 完整性）

- [ ] trends：写方向与拐点，含区间、对照、拟合方式、幅度与单位；不是读数复述
- [ ] anomalies：定位到样本/批次/通道/孔位/时间点，含观察值、期望值、偏离幅度、阈值方法+数值、是否超阈值
- [ ] possible_causes：按证据强度排序；每条含支持证据、排除证据、confound_risk、testability；无排除过程的机制猜测标低置信
- [ ] verification_steps：每条为可执行动作，含 target / expected_if_supported / decision_rule；无空话
- [ ] followup_experiments：对应原因候选与证据缺口，每条含 reason / expected_outcome
- [ ] 覆盖全部指标、对照、重复与边界情况，并交代未解释部分

## C. 证据溯源（可核验性）

- [ ] 每条关键结论带 `evidence`（locator + 逐字 quote），可回指具体文件/字段/行
- [ ] 数值结论与 facts.json 一致；引用原文用真实行号
- [ ] 同一指标两来源冲突时两条都记录并标 `source_conflict`
- [ ] 不虚构引用、DOI、数值；缺失写 `pending_human`/`not_reported`

## D. 异常与限制识别（可靠体验：异常处理）

- [ ] 数据缺失、坏编码、坏 JSON、超量程/饱和被识别并写入 errors/degradations
- [ ] 对照缺失、重复数不足被识别，结论降置信并写入 limitations/pending_human
- [ ] 单位/方法/阈值歧义保留原样写入 uncertainty，不擅自换算或统一
- [ ] 缺软件/依赖如实标注 limitations，不编造运行结果

## E. 确定性与可复用（生态贡献）

- [ ] 重复运行同一输入，除 runtime 外结果一致（确定性排序、无随机数依赖）
- [ ] 脚本只用标准库，`--input/--output/--work` 缺省为 /input、/app/output、/app/work
- [ ] 空输入/坏 JSON/坏编码场景退出码 0 且留合法产物
- [ ] 未预置正式案例答案或白板；示意输出明确标注为示例

## 最低可交付判定

A、B、C 全部勾选且 `status` 诚实时，产物可进入人工复核；任一关键结论无证据、或把未经确认事项写成事实，需回退到 P2 修正后再定稿。
