# 评分维度对齐表（本 Skill 的设计核心）

本 Skill 不把评分当作“事后检查”，而是把题目 5 大评分维度逐条映射到**执行流程的每一步**
和**自检项 S1–S16**。Agent 每完成一步，就同时满足一批评分维度；`main.py selfcheck` 输出
`score_alignment`，按维度汇总通过情况。文字自述不能替代实际运行，S10 明确不自我宣称增益，
把对照验证留给平台。

## 1. 实际效果（40 分）

| 子评分点（分值） | 落实环节 | 自检项 | 判据 |
|---|---|---|---|
| 任务完成度（15） | 步骤 2 分面审查写 `draft.json`，产出结构化问题清单而非整篇评语 | S1 | `findings[]` 每条带 `finding_id/facet/location/quote`，非复述 |
| 结果正确性（15） | 步骤 3 `check` 逐字命中 quote，步骤 3/4 按判据标 severity/issue_type | S2、S9 | 每条 quote 命中原文；枚举合法；误报抑制 |
| 结果完整性（5） | 步骤 1/2 确认六类 facet 相关项，全部处理 | S3 | 六 facet 均有 finding 或 `unchecked_items` |
| 结果可核验性（5） | 步骤 2 每条附 location/quote/evidence；步骤 0 落 sources | S4 | `case_id`、来源、locator、quote、依据齐全 |

## 2. 使用增益（25 分）

| 子评分点（分值） | 落实环节 | 自检项 | 判据 |
|---|---|---|---|
| 质量增益（15） | 结构固定的 `verdict_summary.counts/facet_counts`，可与基础 Agent 逐项比对 | S5 | counts 与 facet_counts 存在 |
| 效率增益（5） | 步骤 0 前 3 分钟先落盘 partial 骨架，此后只增量改进 | S6 | `runtime.phases` 记录 init→build |
| 增益可验证性（5） | 确定性字段与易变量分离：findings/counts 稳定，时间戳只进 runtime | S7 | 重复运行结构一致 |

## 3. 创新增益（15 分）

| 子评分点（分值） | 落实环节 | 自检项 | 判据 |
|---|---|---|---|
| 新增能力（5） | 可定位到原句的问题清单、分面审查、引用主张对照 | S8 | 每条带 location+quote |
| 方法改进（5） | 严重度/性质/置信度三级枚举 + 误报抑制规则 | S9 | 枚举全部合法 |
| 创新证据（5） | 不自我打分，把边界与价值留给平台固定案例对照 | S10 | 无伪造分数键，limitations 如实标注 |

## 4. 可靠体验（10 分）

| 子评分点（分值） | 落实环节 | 自检项 | 判据 |
|---|---|---|---|
| 调用与易用性（3） | `python3 scripts/main.py --help` 退出 0；相对路径调用 | S11 | SKILL.md 用相对路径且存在 |
| 运行稳定性（4） | 不同篇幅/学科/排版可运行；重复运行一致 | S12 | result.json 必填键齐备、合法 |
| 异常处理（3） | 坏 JSON/缺材料/坏 PDF/扫描件/超长均退出 0 且如实降级 | S13 | `status` 枚举合法、errors/degradations 为列表 |

## 5. 生态贡献（10 分）

| 子评分点（分值） | 落实环节 | 自检项 | 判据 |
|---|---|---|---|
| 文档完整性（3） | SKILL.md 写清角色/输入/输出/流程/预算/降级/自检 | S14 | frontmatter name/description/version 齐备 |
| 资源可复用性（4） | 脚本仅标准库、检查清单/判据/自检脚本可复用 | S15 | 脚本无第三方 import，references 齐备 |
| 分享推广价值（3） | SKILL.md 引用的相对路径全部真实存在，可独立复现 | S16 | 引用路径无缺失 |

## 6. 与执行步骤的对应

| 步骤 | 命令 | 对应评分维度 |
|---|---|---|
| 0 落盘骨架 | `init` | 效率增益 S6、可靠体验 S13 |
| 1 通读 | `show` + 读 case.json/task.md | 任务完成度、结果完整性 S3 |
| 2 分面审查 | 写 `draft.json` | 实际效果 S1/S4、创新增益 S8 |
| 3 机械校验 | `check` | 结果正确性 S2、方法改进 S9 |
| 4 成稿 | `build` | 质量增益 S5、可核验性 S4 |
| 5 复检 | `selfcheck` | 全部 S1–S16，输出 score_alignment |

自检项的具体判据见 `scripts/main.py` 的 `SCORE_ALIGNMENT` 与 `_check_s`，两者保持同一份定义，
文档与实现不会漂移。
