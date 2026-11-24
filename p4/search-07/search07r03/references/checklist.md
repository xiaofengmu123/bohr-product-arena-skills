# 交付前核对清单（checklist）

提交前在**非作者环境**（不同机器/Python 版本）跑一遍；每项后标注对应评分维度。全部打勾才算可交付。

## A. 运行自检（可靠体验 / 可靠）

- [ ] `python scripts/main.py --help` 退出码 0，五个子命令（scaffold/inspect/run/merge/selftest）均可列出
- [ ] `python scripts/selftest.py` 全部 PASS，退出码 0
- [ ] 空输入目录：scaffold→run 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON：inspect→run 退出码 0，错误写入 `errors`/`degradations`，不崩溃
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`，不报缺参数
- [ ] 同一输入重复运行两次，result.json **字节级一致**（`selftest.py` 已校验）
- [ ] 单文件 >2MB 被跳过并记录，其余材料继续处理

## B. 结果正确性（实际效果 / 效果）

- [ ] 分母来自独立冻结参考集，不是检索结果自身；`reference_set.version` 已冻结
- [ ] 分子=命中数；coverage_rate/omission_rate 可手工复算，无四舍五入掩盖
- [ ] hit_matrix 逐条含 ref_id、status、matched_by、候选标题/标识符，可指回源材料
- [ ] 每条漏检有 category+signals+evidence，回答“哪条查询/字段/来源/时间窗造成”
- [ ] 冲突信息并存并标 `source_conflict`，未抹平
- [ ] 撤稿/无法获取全文/标识冲突/无标识记录被分别识别并标注
- [ ] 没有把“没找到”当“不相关”直接删除；漏检全部保留

## C. 补检闭环（实际效果 + 使用增益）

- [ ] 补充检索记录了新查询式、字段、来源、时间窗
- [ ] 新增命中逐条列出且均来自参考集内真实记录，无虚构
- [ ] marginal_gain 以补检前漏检数为分母计算
- [ ] 残余遗漏逐条给出解释（是否可补救、需要什么）
- [ ] 补检前后命中集合对比真实，未出现“换同义词重跑无新增却报下降”

## D. 溯源与诚实（实际效果 + 可靠体验）

- [ ] evidence_map 每项含 locator + 逐字 quote
- [ ] sources 列出参考集与检索结果的来源文件
- [ ] uncertainty/pending_human/limitations 与 status 一致，不虚报 ok
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数

## E. 可复用与文档（创新增益 + 生态贡献）

- [ ] references/coverage-method.md 与脚本阈值一致（TITLE_FUZZY=0.88、TITLE_ADJUDICATION=0.70、YEAR_TOLERANCE=1）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则有文字定义
- [ ] 参考集模板（reference_set.json 结构）、命中矩阵（hit_matrix.csv）可迁移到新主题
- [ ] SKILL.md 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例标明为示例，不冒充正式案例结果
