# 评分维度对齐（Scoring Alignment）

本文件是本题变体的核心交付：把题目 5 个评分维度、各评分点逐条映射到
“流程步骤 / 自检项 / 脚本核验字段 / 判定口径”。脚本 `scripts/main.py score`
依据本表生成 `scoring_report.json`，但**只输出状态与证据，不产生数值分数**。

## 1. 判定状态语义

| 状态 | 含义 |
|-|-|
| `met` | 该评分点的可自动化部分已由机械检查满足 |
| `partial` | 部分满足（有可自动检测的缺口） |
| `unmet` | 不满足（可自动检测的缺口未处理） |
| `needs_baseline` | 需平台对照白板判定，Skill 无法在运行时自证 |
| `manual` | 提交期人工动作（如填写 GITHUB_URL.txt） |

`score` 不推断任何“分数”，只如实报告状态与证据；“文字自述不能替代实际运行证据”，
故需要对照的评分点一律标 `needs_baseline`，交给平台判定。

## 2. 维度 → 流程 / 自检 / 脚本 映射

### 2.1 实际效果（40 分）

| 评分点(分) | 流程步骤 | 自检项 | 脚本核验字段（scoring_report.json） | 判定口径 |
|-|-|-|-|-|
| 任务完成度(15) | Step 2 | 稿件是完整可编辑章节稿 | `task_completion` | 存在 manuscript.md，字数≥300 且标题数≥3 → met |
| 结果正确性(15) | Step 1–3 | 数值/单位/样本量/图注/DOI 可回溯；quote 是原文 | `result_correctness` | 悬空=0、quote 错配=0、缺溯源=0、裸数字=0 → met；无硬错但有缺口 → partial |
| 结果完整性(5) | Step 2–3 | 关键章节/图表/声明齐备 | `result_completeness` | 缺章节=∅ 且图/表无缺号 → met |
| 结果可核验性(5) | Step 0–4 | 每条论断可指回材料位置；改动留痕 | `result_verifiability` | sources 有 locator+quote、change_log 非空、result 可加载 → met |

### 2.2 使用增益（25 分）

| 评分点(分) | 流程步骤 | 自检项 | 脚本核验字段 | 判定口径 |
|-|-|-|-|-|
| 质量增益(15) | 全流程 | 对照公平、材料一致 | `quality_gain` | **needs_baseline**；本包先通过机械质量门（悬空=0、错配=0、引用悬空=0） |
| 效率增益(5) | Step 0–4 | 先落盘再增量 | `efficiency_gain` | **needs_baseline**；预算已记录于 `runtime.budget_seconds==900` |
| 增益可验证性(5) | Step 3–4 | 确定性、可复核 | `gain_verifiability` | evidence/consistency 两报告落盘且脚本确定性 → met |

### 2.3 创新增益（15 分）

| 评分点(分) | 流程步骤 | 自检项 | 脚本核验字段 | 判定口径 |
|-|-|-|-|-|
| 新增能力(5) | Step 3 | quote 子串核验、裸数字筛查、引用三向核验 | `new_capability` | 三个能力由脚本真实执行并输出 → met |
| 方法改进(5) | Step 1–4 | 材料索引→证据映射→评分对齐回路 | `method_improvement` | material_index + evidence_map 存在 → met |
| 创新证据(5) | Step 4 | scoring_report 由真实检查派生 | `innovation_evidence` | 报告派生自机械检查、非自述 → met |

新增能力与参考实现的关键差异：quote 由“非空即过”升级为**原文子串核验**（`quote_status`）；
新增**裸数字筛查**（`bare_numbers`）与**引用“正文使用/文末定义/元数据”三向核验**
（`undefined_at_end` / `defined_but_unused`），直接强化“写得实、引得对”。

### 2.4 可靠体验（10 分）

| 评分点(分) | 流程步骤 | 自检项 | 脚本核验字段 | 判定口径 |
|-|-|-|-|-|
| 调用与易用性(3) | Step 0 | CLI 缺省路径 + 相对路径 | `usability` | main.py + core_impl.py 存在 → met |
| 运行稳定性(4) | Step 0–4 | 无异常、可复现 | `stability` | result 可加载且 errors 为空 → met |
| 异常处理(3) | Step 0 | 空/坏/超限降级 | `exception_handling` | degradations/errors 字段存在 → met |

`scripts/selftest.py` 在最小夹具上覆盖：空目录降级、坏 JSON 降级、quote 错配检测、
评分报告生成，退出码 0 表示全过。

### 2.5 生态贡献（10 分）

| 评分点(分) | 流程步骤 | 自检项 | 脚本核验字段 | 判定口径 |
|-|-|-|-|-|
| 文档完整性(3) | 提交前 | SKILL.md frontmatter + references | `docs_completeness` | SKILL.md + conventions.md 存在 → met |
| 资源可复用性(4) | 提交前 | 纯标准库脚本 + 清单可复用 | `reusability` | core_impl + selftest + 本文件存在 → met |
| 分享推广价值(3) | 提交期 | GITHUB_URL.txt | `share_value` | **manual**（提交期填写公开链接） |

## 3. 收口动作（Step 4 必做）

1. 运行 `main.py score` 生成 `scoring_report.json`；
2. 把 `partial/unmet` 项对应的缺口补上，或如实写入 `uncertainty`/`pending_human`；
3. 把 `needs_baseline`/`manual` 项原样保留，不在 result.json 中自评分数；
4. 把 `sources` 与 `change_log` 补全后，最终运行 `scripts/selftest.py` 通过。

> 再次强调：本文与 `scoring_report.json` 均不产生分数。评分维度对齐的目的，是让
> 每个评分点都有“可执行的流程动作 + 可勾选的自检项 + 可自动核验的证据字段”，
> 而不是用文字自述替代运行证据。
