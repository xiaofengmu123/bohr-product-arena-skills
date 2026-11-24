# 交付前核对清单（逐条勾选，对应评分维度）

提交前在**非作者环境**（不同机器 / Python 版本）跑 `python scripts/selftest.py`，全部通过才算可交付。
每条后标注评分维度：效果=实际效果、增益=使用增益、创新=创新增益、可靠=可靠体验、生态=生态贡献。

## 运行自检（可靠）

- [ ] `python scripts/main.py --help` 退出码 0，四个子命令均可列出
- [ ] 空输入目录：scaffold→inspect→run 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON：scaffold→run 退出码 0，错误写入 `errors`/`degradations`，不崩溃
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`
- [ ] 同一输入重复运行，coverage_rate 与 hit_matrix 状态一致（无随机性）
- [ ] 单文件 >2MB 被跳过并记录，其余材料继续处理
- [ ] 前 3 分钟 `scaffold` 先落盘合法 partial 骨架，之后任何中断不残留半截 JSON（原子写盘）

## 结果正确性（效果）

- [ ] 分母来自独立冻结参考集，`reference_set.version` 已冻结（SHA-256）
- [ ] coverage_rate/omission_rate 可手工复算，保留 6 位小数，无四舍五入掩盖
- [ ] hit_matrix 逐条含 ref_id、status、matched_by、title_similarity、候选标题/标识符/来源
- [ ] 每条漏检有 category + signals + evidence，落到具体查询式/字段/来源/时间窗
- [ ] 撤稿/无法获取全文/标识冲突/无标识记录分别标 `record_flags`，未抹平
- [ ] 重复记录被去重并标 `duplicate_of`，分母只计唯一记录
- [ ] 没有把"没找到"当"不相关"删除；漏检全部保留在 `omissions`

## 补检闭环（效果、增益）

- [ ] 补充检索记录新查询式、字段、来源、时间窗
- [ ] 新增命中逐条列出，均来自材料真实记录且 quote 逐字核验
- [ ] marginal_gain 以补检前漏检数为分母计算
- [ ] residual_omissions 逐条解释（是否可补救、需要什么）
- [ ] 补检前后命中集合对比真实，未出现"换同义词重跑无新增却报下降"

## 溯源与诚实（效果、可靠）

- [ ] evidence_map 每项含 locator + 逐字 quote，并区分 derived/verbatim
- [ ] sources 列出参考集与检索结果的来源文件及角色
- [ ] uncertainty/pending_human/limitations 与 status 一致，不虚报 ok
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数
- [ ] runtime 记录阶段耗时与预算剩余，超时标 `time_budget_exceeded`

## 可复用与文档（创新、生态）

- [ ] references/coverage-method.md 与脚本阈值一致（title_fuzzy=0.88、title_adjudication=0.70、year_tolerance=1）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则有文字定义
- [ ] 参考集模板（reference_set.json）、命中矩阵（hit_matrix.csv）、updates schema 可迁移到新主题
- [ ] SKILL.md 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例（selftest.py 内合成夹具）标明为示例，不冒充正式案例结果
