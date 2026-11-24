# 交付前核对清单（逐条勾选，对应评分维度）

提交前在**非作者环境**（不同机器/Python 版本）跑一遍 `python scripts/main.py selftest`，全部打勾才算可交付。每条末尾标注评分维度：效果=实际效果、增益=使用增益、创新=创新增益、可靠=可靠体验、生态=生态贡献。

## 运行自检（可靠）

- [ ] `python scripts/main.py --help` 退出码 0，六个子命令（scaffold/inspect/run/merge/finalize/selftest）均可列出
- [ ] 空输入目录：scaffold→run 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON：scaffold→run 退出码 0，错误写入 `errors`/`degradations`，不崩溃
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`，不报缺参数
- [ ] 同一输入重复运行两次，`coverage_rate` 与 `hit_matrix` 状态完全一致（无随机性）
- [ ] 单文件 >2MB 被跳过并记录，其余材料继续处理
- [ ] `scripts/main.py selftest` 全部 PASS（27+ 项，无网络、无外部输入）

## 结果正确性（效果）

- [ ] 分母来自独立冻结参考集，不是检索结果自身；`reference_set.version` 已冻结
- [ ] 分子=命中数，`coverage_rate`/`omission_rate` 可手工复算，无四舍五入掩盖
- [ ] `hit_matrix` 逐条含 ref_id、status、matched_by、confidence、similarity、ref_title、result_title、result_locator
- [ ] 每条漏检有 `category`+`query_id`+`field`+`source`+`time_window`+`evidence`，回答“哪条查询/字段/来源/时间窗造成”
- [ ] 冲突信息并存并标 `source_conflict`，未抹平
- [ ] 撤稿/无法获取全文/标识冲突/无标识记录被分别识别并标注
- [ ] 没有把“没找到”当“不相关”直接删除；漏检全部保留

## 补检闭环（效果、增益）

- [ ] 补充检索记录了新查询式、字段、来源、时间窗
- [ ] 新增命中逐条列出且均来自材料中真实记录，无虚构
- [ ] `marginal_gain` 以补检前漏检数为分母计算
- [ ] 残余遗漏逐条给出解释（是否可补救、需要什么）
- [ ] 补检前后命中集合对比真实，未出现“换同义词重跑无新增却报下降”

## 降级与不确定性（效果、可靠）—— 本版重点

- [ ] 每处不确定结论都有 `uncertainty` 条目：`item`、`kind`（evidence_gap/conflict/threshold_boundary/tool_missing/source_timeout）、`affects`（影响哪个字段）、`severity`、`resolution`
- [ ] 覆盖率给出 `coverage_lower`/`coverage_upper` 区间，而非只给单点，裁决边界被显式暴露
- [ ] 每条命中/漏检带 `confidence`（high/medium/low/none），低置信度不冒充高置信度
- [ ] 缺数据、缺软件（如无 pdftotext）、来源超时 → 写入 `limitations`/`degradations`，不编造
- [ ] `status` 与 `uncertainty`/`pending_human`/`limitations` 一致：有 pending 裁决即 `partial`，绝不虚报 `ok`

## 溯源与诚实（效果、可靠）

- [ ] `evidence_map` 每项含 locator + 逐字 quote
- [ ] `sources` 列出参考集与检索结果的来源文件
- [ ] `uncertainty`/`pending_human`/`limitations` 与 `status` 一致
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数

## 可复用与文档（创新、生态）

- [ ] `references/coverage-method.md` 与脚本阈值一致（title_fuzzy=0.88、title_adjudication=0.70、year_tolerance=1、dedup_title=0.95）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则有文字定义
- [ ] 参考集模板（`reference_set.json`）、命中矩阵（`hit_matrix.csv`）、`updates.json` 结构可迁移到新主题
- [ ] SKILL.md 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例标明为示例，不冒充正式案例结果
