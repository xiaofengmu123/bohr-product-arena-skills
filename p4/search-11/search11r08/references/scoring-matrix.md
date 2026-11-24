# 评分维度 → 流程 → 自检项 对齐表

本表把题目 5 个评分维度（共 16 个评分点，合计 100 分）逐条映射到本 Skill 的
执行步骤、证据落点与自检项。设计取舍：**每个评分点都必须能在 result.json 或
SKILL.md 中找到对应物，而非依赖文字自述**。执行时按此表自检，收尾前跑
`scripts/selftest.py` 验证结构，再按本节逐条核对内容。

## 1. 实际效果（40 分）

| 评分点 | 分值 | 对应流程步骤 | result.json 落点 | 自检项 |
|---|---|---|---|---|
| 任务完成度 | 15 | Step 1（界定问题）→ Step 3（取回结果） | `research_question`、`retrieval_scope`、`patent_records`、`literature` | 是否在授权范围内走通"识别对象→选数据源→取回→交付"完整链路；`status` 非空 |
| 结果正确性 | 15 | Step 2（号码规范化/同族/法律状态核验） | `patent_records[*].number_type`、`families[*].family_type`、`legal_status[*]` | 公开号/申请号/授权号真实存在且格式规范；同族与法律状态对得上官方登记；权利要求不越界 |
| 结果完整性 | 5 | Step 3（五问全覆盖） | `patent_records`+`families`+`legal_status`+`literature`+`authorization` | 覆盖"保护什么/同族关系/是否有效/还有谁做/是否有权展示"五件事 |
| 结果可核验性 | 5 | Step 4（证据对齐） | `sources`、`evidence[*].locator`+`quote`、`change_log` | 检索式、来源版本、查询日期、原始返回、字段抽取规则、人工核验均可复跑 |

## 2. 使用增益（25 分）

| 评分点 | 分值 | 对应流程步骤 | 落点 | 自检项 |
|---|---|---|---|---|
| 质量增益 | 15 | 全流程（尤其 Step 2 规范化与 Step 3 官方核验） | `patent_records`、`legal_status`、`uncertainty` | 相比基础 Agent 是否减少"关键词硬搜/号码混用/转载页当官方/族关系错并"四类错误 |
| 效率增益 | 5 | Step 2/3 自动化脚本 | `sources[*]`、`runtime.elapsed_seconds` | 是否减少逐条回官方库核对与跨库切换工时（复用 normalize/extract/family） |
| 增益可验证性 | 5 | Step 4 记录口径 | `sources[*].retrieved_at`、`change_log` | 同批专利/主题、同权限、同评分口径下可公平对照 |

## 3. 创新增益（15 分）

| 评分点 | 分值 | 对应流程步骤 | 落点 | 自检项 |
|---|---|---|---|---|
| 新增能力 | 5 | Step 2 | `scripts/main.py` normalize/extract/family | 号码规范化、同族自动归并并标注口径、OCR 边界合并、受限数据降级是否基础 Agent 不具备 |
| 方法改进 | 5 | Step 2/3 | `families[*].family_type`、`legal_status[*].staleness_risk` | 检索式构造、同族判定规则、字段映射、状态核验流程是否实质改进 |
| 创新证据 | 5 | 交付文档 | `references/scoring-matrix.md`、SKILL.md §5/§6 | 是否说明创新点边界与价值，比较对象（商用库/通用检索 Agent/人工查新）明确 |

## 4. 可靠体验（10 分）

| 评分点 | 分值 | 对应流程步骤 | 落点 | 自检项 |
|---|---|---|---|---|
| 调用与易用性 | 3 | Step 0 | `scripts/main.py --help` 退出码 0 | Agent 能否正确触发；用户能否按说明完成一次含号码定位+主题检索的完整任务 |
| 运行稳定性 | 4 | 全流程 | `runtime`、确定性输出 | 不同库/国家/语言输入、重复运行下持续产出可用结果 |
| 异常处理 | 3 | §6 降级策略 | `errors`、`degradations`、`limitations`、`uncertainty` | 限流/鉴权失败/检索无命中/法律状态冲突/字段缺失/授权不足六类异常识别与处置 |

## 5. 生态贡献（10 分）

| 评分点 | 分值 | 对应流程步骤 | 落点 | 自检项 |
|---|---|---|---|---|
| 文档完整性 | 3 | 交付文档 | SKILL.md + references/*.md | 使用说明、依赖、字段口径、授权范围、示例、限制、维护信息齐全 |
| 资源可复用性 | 4 | 脚本与规则 | `scripts/`（纯标准库）+ 规则表 | Skill 本体、脚本、检索式模板、字段映射表、同族归并规则可被他人复用 |
| 分享推广价值 | 3 | 案例沉淀 | `change_log`、`references/rules-and-checklist.md` | 沉淀可分享的查新案例、同族与状态处理方法、授权核验经验 |

## 使用方式

执行前：通读本表 → 判定案例类型（号码定位 / 主题检索）→ 按对应行执行。
收尾前：跑 `scripts/selftest.py` 验证结构，再逐行勾选本表自检项，任何一行
无法在 result.json 定位到证据，视为未完成，补全后再 finalize。
