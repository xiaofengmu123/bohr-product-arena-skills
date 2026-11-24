# 方法与检查清单

## 六要素拆解

把研究问题拆成可检索子问题（SQ1..SQn），每条标注：

1. 对象 object：研究的主体/材料/人群
2. 机制 mechanism：作用机理、因果关系
3. 方法 method：实验/统计/建模方法
4. 时间范围 time_range：年份窗或时期
5. 比较关系 comparison：对比组、干预与对照
6. 证据要求 evidence_requirement：需要什么样的证据才能回答

## 依赖化查询规划

- 先跑"前置"查询（界定对象与范围），再跑依赖其结果的查询。
- 相互独立的子问题可并行检索，减少无效轮次。
- 每个查询记录 `rationale`（为什么这么查）与 `depends_on`。

## 受控术语扩展

- 每概念 ≤6 变体：同义词、缩写、跨语言、上下位、领域变体。
- 严重歧义时注明歧义并加限定词，或拆成两条查询分别记录。
- 目的：提高召回、控制发散；不追求穷举。

## 多轮检索与停止判断

- 每轮记录 query/source/database/timestamp/rationale/kind。
- 停止阈值示例：每个子问题 ≥1 条 included 且 evidence 有 locator+quote；或连续两轮边际收益（新增 included 数）为 0。
- 冲突证据全部保留，不擅自取舍；无法判断写 `pending_human`。

## 全量筛选

- 候选记录**逐条**给 `included/excluded/uncertain/duplicate` + 理由，排除项不静默丢弃。
- 重复记录合并到 `merged_duplicates`，注明并入哪条。

## 自检清单（对应评分维度，逐条勾选）

实际效果（40）：
- [ ] 完成度：≥1 条可检索子问题，每条含六要素；每步输入输出清楚
- [ ] 正确性：结果标题/作者/年份/期刊/DOI 真实且逐字保留；relevance_reason 指向 locator+quote
- [ ] 完整性：每个子问题 ≥1 条 included；未覆盖列入 `uncertainty.coverage_gaps`
- [ ] 可核验性：`evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖全部记录

使用增益（25）：
- [ ] 质量增益：多轮规划与术语扩展提高召回；结果有 score+breakdown
- [ ] 效率增益：查询去重、排除有理由，减少返工
- [ ] 增益可验证：`selfcheck` 全 PASS；case_id+status 存在

创新增益（15）：
- [ ] 新增能力：六要素拆解字段齐全
- [ ] 方法改进：依赖化规划 + 透明打分脚本固化
- [ ] 创新证据：stop_decision 有明确阈值/理由

可靠体验（10）：
- [ ] 调用易用：`--help` 退出 0，路径缺省 `/input` `/app/output` `/app/work`
- [ ] 运行稳定：空输入/坏 JSON/坏编码不崩溃；重复运行幂等
- [ ] 异常处理：errors/degradations/limitations 记录，status 诚实

生态贡献（10）：
- [ ] 文档完整：SKILL.md + references 齐全
- [ ] 资源复用：拆解模板、查询模板、检查清单可复用
- [ ] 分享价值：他人可独立复现完整检索

运行 `python3 -B scripts/selftest.py` 做机器自检（空目录/坏 JSON/坏编码/幂等/契约回填）。
