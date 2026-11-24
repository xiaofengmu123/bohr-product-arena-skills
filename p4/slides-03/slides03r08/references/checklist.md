# 评分维度对齐清单（对应评分维度逐条勾选）

把题目评分维度逐条映射到流程步骤、result.json 字段与自检脚本。每项同时给出自动核验方式或人工检查方式。**selftest 只输出逐项通过/未通过，不产生评测分数。**

## 实际效果（40 分）

| 评分点 | 分值 | 落地字段 | 检查方式 |
|-|-|-|-|
| 任务完成度 | 15 | report_goal / audience / narrative / pages / qa_prep | selftest“闭环”项；四者齐全且互相对应 |
| 结果正确性 | 15 | pages.single_claim / pages.figures / pages.evidence_strength / evidence_map.quote | evidence_check：逐页唯一主张、数字与图表一致、强度匹配 |
| 结果完整性 | 5 | 六方面：论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染 | selftest coverage 项 |
| 结果可核验性 | 5 | evidence_map(locator+quote) / sources / change_log | evidence_check + selftest 可核验项 |

- [ ] 页面上的每一个论断都能在源材料里找到对应位置。
- [ ] 叙事顺序服务于指定听众与时长。
- [ ] 从源材料到成稿的取舍过程可被他人复核。

## 使用增益（25 分）

| 评分点 | 分值 | 落地字段 | 检查方式 |
|-|-|-|-|
| 质量增益 | 15 | narrative.ordering_rationale / tradeoffs | 对比“逐节搬运”是否减少返工页数 |
| 效率增益 | 5 | change_log 中返工页数与导出修正次数 | 记录而非口头自述 |
| 增益可验证性 | 5 | change_log 对照记录 | 取舍决策写入后可复核 |

- [ ] 相同输入、相同听众与时长下，比“逐节搬运”减少人工返工。
- [ ] 取舍决策（为什么某图不上页、为什么调整顺序）已写入 narrative 或 change_log。

## 创新增益（15 分）

| 评分点 | 分值 | 落地字段 | 检查方式 |
|-|-|-|-|
| 新增能力 | 5 | audience（先验/关注点/质疑） | selftest 听众适配项 |
| 方法改进 | 5 | pages 唯一主张 + 证据 + 图表绑定 | selftest 页级规划项 |
| 创新证据 | 5 | qa_prep（机制/数据/对照/局限） | selftest 问答预演项 |

- [ ] 听众适配、页级规划、问答预演均有落地字段，非空话。
- [ ] 叙事模板、内容取舍规则、图表引用绑定带来实质改进。

### 听众类型 → 汇报侧重（听众适配决策表）

| 听众 | 报告目标侧重 | 术语深度 | 证据深度 | 常见质疑 |
|-|-|-|-|-|
| 组会 / 合作者 | 讲清方法与进度 | 中，解释核心术语 | 结果为主 | 进度风险、下一步 |
| 答辩 / 评委 | 讲清创新点与边界 | 高，专业术语 | 方法与对照 | 新颖性、贡献边界 |
| 评审 | 讲清结论与证据强度 | 中高 | 证据强度分层 | 数据可靠性、局限 |

汇报目标必须转成可检查结果：例如“让评委认可方法新颖性”要落到“第 N 页给出与 SOTA 的定量对照，且证据强度标 high”；“让合作者理解进度风险”要落到“第 M 页列出未完成项与依赖”。

## 可靠体验（10 分）

| 评分点 | 分值 | 落地字段 | 检查方式 |
|-|-|-|-|
| 调用与易用性 | 3 | SKILL.md 触发条件 | 人工核验 |
| 运行稳定性 | 4 | status / 可编辑文件 / PDF/PNG 预览 | selftest 渲染预览项 |
| 异常处理 | 3 | uncertainty / degradations / pending_human | selftest 异常项 |

- [ ] 缺图、图表分辨率不足、公式或符号丢失、模板冲突、字体缺失、导出失败、超时均已识别并标注。

### 异常 → 降级处置表

| 异常 | 处置 | 标注字段 |
|-|-|-|
| 论文缺图 / 图不可用 | 页面注明“图缺失 / 不可用”，不生成伪造图 | rendering + uncertainty |
| 图表分辨率不足 | 保留原图并标注分辨率，不放大伪造 | rendering + degradations |
| 公式 / 符号丢失 | 用源材料逐字文字表述代替，标注公式缺失 | evidence_map.quote + uncertainty |
| 模板与内容冲突 | 记录冲突项，退回兼容布局 | degradations + change_log |
| 字体缺失 | 用 font-discovery 找替代字体并记录 | rendering.fonts + degradations |
| 导出失败 / 超时 | 保留可编辑源文件，status 标 partial/failed | status + change_log |
| 软件状态缺失 | 只标 limitations/pending_human，禁止编造 | pending_human |

## 生态贡献（10 分）

| 评分点 | 分值 | 落地对象 | 检查方式 |
|-|-|-|-|
| 文档完整性 | 3 | SKILL.md 用途/适用/调用/输入输出/异常 | selftest 文档项 |
| 资源可复用性 | 4 | scripts 仅标准库、相对路径、--help | selftest 资源项 |
| 分享推广价值 | 3 | references 字段定义与评分对齐清单 | selftest 推广项 |

## 收尾必查

- [ ] `result.json` 含 `case_id`、output_contract 全字段、`status` / `sources` / `uncertainty` / `pending_human` / `change_log`。
- [ ] `status` 如实为 `complete` / `partial` / `failed`，未编造评测分数或运行结果。
- [ ] 前 180 秒已落盘骨架，之后仅为增量改进。
- [ ] 脚本路径用相对路径（scripts/xxx.py），仅依赖 Python 标准库。
