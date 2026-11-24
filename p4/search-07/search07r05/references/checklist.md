# 交付前核对清单（逐条勾选，对应评分维度）

提交前在**非作者环境**（不同机器/Python 版本）跑一遍 `python scripts/selftest.py`，全部打勾才算可交付。每条后标注对应评分维度：效果=实际效果、增益=使用增益、创新=创新增益、可靠=可靠体验、生态=生态贡献。

## 运行自检（可靠）

- [ ] `python scripts/main.py --help` 退出码 0，五个子命令（scaffold/inspect/run/merge/validate）均可列出
- [ ] 空输入目录：scaffold→run 退出码 0，result.json 合法且 status=`insufficient_evidence`
- [ ] 坏 JSON：scaffold→run 退出码 0，错误写入 `errors`/`degradations`，不崩溃
- [ ] 缺 `--input/--output/--work`：默认指向 `/input`、`/app/output`、`/app/work`（`coverage.py` 常量），且可置于子命令之后
- [ ] 同一输入重复运行两次，`coverage_rate`、`hit_matrix` 状态完全一致（无随机性，全部排序稳定）
- [ ] 单文件 >2MB 被跳过并记录到 `degradations`，其余材料继续处理
- [ ] `python scripts/selftest.py` 打印 `SELFTEST PASS`，退出码 0

## 结果正确性（效果）

- [ ] 分母来自独立冻结参考集，不是检索结果自身；`reference_set.version` 已冻结
- [ ] 分子=命中数，`coverage_rate`/`omission_rate` 可手工复算，保留 4 位小数不掩盖
- [ ] `hit_matrix` 逐条含 `ref_id/status/matched_by/候选标题/候选标识符/候选locator`
- [ ] 每条漏检有 `category+signals+evidence` 及 `query|source|time_window_attribution`，回答“哪条查询/字段/来源/时间窗造成”
- [ ] 冲突信息并存并标 `title_conflict_same_doi`，未抹平
- [ ] 撤稿/无法获取全文/标识冲突/无标识记录在 `identifier_issues` 或 `status_report` 中分别识别
- [ ] 没有把“没找到”当“不相关”删除；漏检全部保留在 `omissions`

## 输出契约对齐（效果、创新）

- [ ] `python scripts/main.py validate --output <dir>` 打印 aligned，退出码 0
- [ ] `result.json` 含 25 个顶层键，`case_id` 非空，`status` 在枚举内
- [ ] `case.json` 的 `output_contract` 被逐字回填；无该键时写入 `source=runtime_contract` 的推导契约
- [ ] `search_results` / `evidence` / `status_report` 三个运行时契约区域均已填充
- [ ] `change_log` 非空、revision 单调递增、每条含 action/fields/time_utc
- [ ] `sources/uncertainty/pending_human/change_log` 与 `status` 一致，不虚报 ok

## 补检闭环（效果、增益）

- [ ] 补充检索记录新查询式、字段、来源、时间窗
- [ ] 新增命中逐条列出且均来自材料中真实记录（`evidence_locator + quote`），无虚构
- [ ] `marginal_gain` 以补检前漏检数为分母
- [ ] 残余遗漏逐条给出解释（是否可补救、需要什么）
- [ ] 补检前后命中集合对比真实，未出现“换同义词重跑无新增却报下降”

## 溯源与诚实（效果、可靠）

- [ ] `evidence_map` 每项含 `locator + quote + conclusion`
- [ ] `sources` 列出参考集与检索结果的来源文件与角色
- [ ] `uncertainty/pending_human/limitations` 与 `status` 一致
- [ ] 未编造 DOI、年份、命中、检索结果、评测分数

## 可复用与文档（创新、生态）

- [ ] `references/contract.md` 与脚本阈值一致（title_fuzzy=0.88、title_adjudication=0.70、year_tolerance=1）
- [ ] 覆盖率口径、命中规则、漏检分类、补检停止规则有文字定义
- [ ] 参考集/命中矩阵/分类表可迁移到新主题；脚本仅依赖标准库
- [ ] `SKILL.md` 写清调用方式、输入输出契约、异常处理与自查方法
- [ ] 最小调用示例标明为示例，不冒充正式案例结果
