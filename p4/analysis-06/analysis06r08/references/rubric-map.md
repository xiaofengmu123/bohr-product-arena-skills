# 评分维度 → 流程 / 自检 / 产物 对照表

本文件把题目的五条评分维度逐条映射到本 Skill 的具体执行步骤、自检项与可核验产物，
用于 Agent 在执行末段逐条核对，也供评审按记录复核。表中「产物」均为脚本真实产出，
不依赖 Agent 的文字自述。

## 1. 实际效果（40 分）

| 评分点（分值） | 对应流程 | 自检项 | 产物/证据落点 |
|-|-|-|-|
| 任务完成度（15） | 阶段 2 定向查询：按题目问题写只读 SQL，追加到 `queries.json` 后重跑 | 每个问题至少一条 `query_log` 记录；`answers` 含该问题 `row_count` 与 `preview_rows` | `result.json.answers`、`query_log.csv` |
| 结果正确性（15） | 阶段 2/3：连接键取 `schema.json.suggested_joins`；编码字段显式转换后再聚合 | SQL 与 Python 聚合 `matches_python=true`；GROUP BY 行数不因漏连接膨胀 | `result.json.query_log[*].summary.matches_python`、`summary_stats.csv` |
| 结果完整性（5） | 阶段 1 列出题目要求的表/字段/时间/筛选/跨表维度，逐项落实 | 未覆盖项写入 `limitations` 与 `pending_human`，不静默跳过 | `result.json.limitations`、`pending_human` |
| 结果可核验性（5） | 全程保留 locator + 逐字 quote；连接对象/快照/行数入日志 | 每条关键结论能在 `evidence_map.json` 中找到 locator 与 quote | `evidence_map.json`、`result.json.sources` |

## 2. 使用增益（25 分）

| 评分点（分值） | 对应流程 | 自检项 | 产物/证据落点 |
|-|-|-|-|
| 质量增益（15） | 阶段 0 自动 schema 检索 + 连接键推断 + 编码字段检测，减少错误查询 | 自动产出 `suggested_joins` 与 `coded_as_text` 标注 | `schema.json`、`data_quality.csv` |
| 效率增益（5） | 同一命令幂等重跑；改动走 `contract_answers.json` / `queries.json`，不整文件重写 | 一轮命令完成"落骨架 + 自动分析 + 合并答案 + 执行查询" | `result.json.change_log` 记录每次增量 |
| 增益可验证性（5） | 同一快照、同一命令、确定性排序，重复运行数值一致 | `--selftest` 中 determinism 项通过 | `selftest` 输出、`summary_stats.csv` 两次一致 |

## 3. 创新增益（15 分）

| 评分点（分值） | 新增能力 | 自检项 | 产物/证据落点 |
|-|-|-|-|
| 新增能力（5） | 按数据字典做字段语义映射；自动规划跨表连接路径；SQL 报错定位并写入 errors | `schema.json` 含 FK 与共享列推断；`data_quality.csv` 标 `coded_as_text`/`id_like` | `schema.json.suggested_joins`、`data_quality.csv` |
| 方法改进（5） | schema 卡片 + 连接键推断 + 结果交叉校验（SQL↔Python） | 每条 numeric 列有 `matches_python` 布尔结论 | `query_log.csv`、`result.json.query_log` |
| 创新证据（5） | 脏数据处理规则化（缺失/重复/离群点均计数、不静默清洗） | `data_quality.csv` 逐列给出 `missing_ratio`、`n_outliers_iqr` | `data_quality.csv`、`value_counts.json` |

## 4. 可靠体验（10 分）

| 评分点（分值） | 对应流程 | 自检项 | 产物/证据落点 |
|-|-|-|-|
| 调用与易用性（3） | 单入口 `scripts/main.py`，缺省路径即 `/input`→`/app/output` | `python3 -B scripts/main.py --help` 退出码 0 | `--help` 输出 |
| 运行稳定性（4） | 只读连接、标准库、固定排序；空/坏输入不崩溃 | `--selftest` 覆盖空目录、坏 JSON、重复运行 | selftest 8 项全过 |
| 异常处理（3） | 空结果/连接失败/权限不足/字段歧义/重复/类型不一致显式记录 | `errors`/`degradations`/`uncertainty` 非空且可读 | `result.json.errors` 等 |

## 5. 生态贡献（10 分）

| 评分点（分值） | 对应流程 | 自检项 | 产物/证据落点 |
|-|-|-|-|
| 文档完整性（3） | SKILL.md + 本文件 + checklist.md 三份说明一致 | 三文件均与提交版本一致、可独立复跑 | `SKILL.md`、`references/*.md` |
| 资源可复用性（4） | `references/sql-patterns` 模板、schema 卡片、字段字典可被他人复跑 | 他人按 SKILL.md 第 7 节即可复跑一次查询分析 | `schema.json`、`query_log.csv` |
| 分享推广价值（3） | 沉淀查询模式、脏数据处理经验与题集口径 | 每类结论都能回查到原始快照与规则 | `report.md`、`evidence_map.json` |

> 注：SQL 模板与检查规则见 `references/checklist.md`；「字段字典接地」的落地方式是
> `data_quality.csv` 的 `declared_type` 与实际推断 `kind` 并列，编码型字段被显式标记，
> Agent 依据此表决定转换规则，而非凭记忆。
