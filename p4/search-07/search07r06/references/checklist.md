# 交付前核对清单（逐条勾选，对应评分维度）

提交前在**非作者环境**（不同机器/Python 版本）跑一遍 `python scripts/selftest.py`，
全部打勾才算可交付。每条后标注对应评分维度：效果=实际效果、增益=使用增益、
创新=创新增益、可靠=可靠体验、生态=生态贡献。

## 运行自检（可靠）

- [ ] `python scripts/main.py --help` 退出码 0，八个子命令均可列出
- [ ] `python scripts/selftest.py` 全过（空目录、坏 JSON、缺参考集、正常样例、确定性、体积）
- [ ] 空输入目录：full 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON/坏编码：不崩溃，错误写入 `errors`/`degradations`
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`
- [ ] 同一输入重复运行，coverage_rate 与 hit_matrix 状态完全一致（无随机性；时间戳/哈希为运行时元数据，允许变化）
- [ ] 单文件 >2MB 被跳过并记录，其余材料继续处理

## 结果正确性（效果）

- [ ] 分母来自独立冻结参考集，不是检索结果自身；`reference_set.version/content_hash` 已冻结
- [ ] 分子=命中数，coverage_rate/omission_rate 可手工复算，无四舍五入掩盖
- [ ] hit_matrix 逐条含 ref_id、status、matched_by、confidence、候选标识/标题、证据 locator
- [ ] 每条漏检有 category+query/field/source/time_window+evidence，回答“哪条查询/字段/来源/时间窗造成”
- [ ] 冲突信息并存并标 `identifier_conflict`/`source_conflict`，未抹平
- [ ] 撤稿/无法获取全文/标识冲突/无标识/重复记录分别识别标注
- [ ] 没有把“没找到”当“不相关”删除；漏检全部保留

## 补检闭环（效果、增益）

- [ ] 补充检索记录了新查询式、字段、来源、时间窗
- [ ] 新增命中逐条来自材料真实记录，无虚构；`new_hits` 非法 ref_id 被剔除并记 degradations
- [ ] marginal_gain 以补检前漏检数为分母计算
- [ ] 残余遗漏逐条给出解释（可补救/需要什么）
- [ ] 补检前后命中集合对比真实，未出现“换同义词重跑无新增却报下降”

## 溯源与诚实（效果、可靠）

- [ ] evidence_map 每项含 locator + 逐字 quote
- [ ] sources 列出参考集与检索结果来源文件
- [ ] uncertainty/pending_human/limitations 与 status 一致，不虚报 ok
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数

## 中间表示与可复核（创新）

- [ ] plan.json 先于 build/match 冻结研究问题、范围、命中规则与补检触发/停止条件
- [ ] reference_set.json / search_index.json / match_model.json / attribution_model.json 齐全且含哈希
- [ ] result.json 由 merge 派生，`pipeline.intermediates` 列出各中间产物 sha256
- [ ] 复核者可按哈希校验并重跑 merge 复算覆盖率
- [ ] 说明“中间表示+merge 派生”相对“以结果定义分母/手写主结果”的边界

## 可复用与文档（创新、生态）

- [ ] references/coverage-method.md 与脚本阈值一致（title_auto=0.88、title_adjudication=0.70、year_tolerance=1）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则有文字定义
- [ ] 参考集模板（reference_set.json 结构）、命中矩阵（hit_matrix.csv）可迁移到新主题
- [ ] SKILL.md 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例（selftest 合成样例）标明为示例，不冒充正式案例结果
