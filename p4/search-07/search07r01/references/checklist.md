# 交付前核对清单（逐条勾选，对应评分维度）

提交前在非作者环境（不同机器 / Python 版本）跑一遍 `python scripts/selftest.py`，
全部打勾才算可交付。每行标注对应评分维度：效果=实际效果、增益=使用增益、创新=创新增益、可靠=可靠体验、生态=生态贡献。

## 运行自检（可靠）

- [ ] `python scripts/main.py --help` 退出码 0，四个子命令 scaffold/inspect/run/merge 均可列出
- [ ] 空输入目录：scaffold→run 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON：scaffold→inspect→run 退出码 0，错误写入 `errors`/`degradations`，不崩溃
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`
- [ ] 同一输入重复运行两次，coverage_rate 与 hit_matrix 状态完全一致（无随机性）
- [ ] 单文件 >2MB 被跳过并记录，其余材料继续处理

## 结果正确性（效果）

- [ ] 分母来自独立冻结参考集，`reference_set.version` 已冻结，不是检索结果自身
- [ ] 分子=命中数；coverage_rate/omission_rate 可手工复算，无四舍五入掩盖
- [ ] hit_matrix 逐条含 ref_id、status、matched_by、match_score、candidate_title、evidence
- [ ] 每条漏检有 attribution（category + query/field/source/time_window + evidence）
- [ ] 冲突信息并存并标 `source_conflict`，未抹平；撤稿标 `retracted_or_unavailable`
- [ ] 没有把"没找到"当"不相关"删除；漏检全部保留统计

## 补检闭环（效果、增益）

- [ ] 补充检索记录新查询式、字段、来源、时间窗
- [ ] 新增命中逐条来自材料中真实记录，无虚构
- [ ] marginal_gain 以补检前漏检数为分母计算
- [ ] 残余遗漏逐条给出是否可补救及所需条件
- [ ] 补检前后命中集合对比真实，未出现"换同义词无新增却报下降"

## 溯源与诚实（效果、可靠，本版重点）

- [ ] evidence_map 每项含 locator + 逐字 quote + verbatim:true + status
- [ ] 每条结论（命中/漏检/归因/数值）都能指回材料逐字片段，缺失写 `not_reported`
- [ ] sources 列出参考集与检索结果的来源文件
- [ ] uncertainty/pending_human/limitations 与 status 一致，不虚报 ok
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数

## 可复用与文档（创新、生态）

- [ ] references/coverage-method.md 阈值与脚本一致（0.88 / 0.70 / year_tolerance=1）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则均有文字定义
- [ ] 参考集模板、命中矩阵、分类表、脚本可迁移到新主题
- [ ] SKILL.md 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例标明为示例，不冒充正式案例结果
