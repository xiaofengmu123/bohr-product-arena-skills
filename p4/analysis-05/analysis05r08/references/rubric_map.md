# 评分维度 → 流程 → 自检项 对齐表

本文件把 `challenge.json` 的五个评分维度逐条映射到本 Skill 的流程阶段、产出文件与自检项。目的：让 Agent 在预算内优先产出与评分点直接对应的证据，而不是只产出结论。

## 映射总表

| 评分维度 | 分值 | 考察内容 | 落点阶段 | 产出证据 |
|---|---|---|---|---|
| 实际效果 | 40 | 五段串通、数值口径、检查点覆盖、证据链可重跑 | clean/analyze/render/report | cleaned.csv、clean_meta.json、stats.json、figure_*.svg、report.md、sources |
| 使用增益 | 25 | 质量稳定提升、减少改代码/重跑、公平对照 | scaffold 骨架、幂等、确定性 | result.json 骨架、change_log、selftest 确定性断言 |
| 创新增益 | 15 | 新增能力、方法改进、创新证据 | profile 自动发现、stdlib 统计、SQLite 查询 | profile.json、stats.json.backend、clean_meta 一致性 |
| 可靠体验 | 10 | 触发调用、多形态/规模稳定、异常处置 | 全阶段异常路径 | errors、degradations、uncertainty、退出码 0 |
| 生态贡献 | 10 | 文档、资源可复用、可迁移流程 | SKILL.md、references、selftest.py | 目录结构、字段字典、自检全绿 |

## 逐维说明

### 1. 实际效果（40 分）

- 任务完成度（15）：五段在**同一环境**串通，不能只给截图。本 Skill 的 `run` 子命令顺序执行 scaffold→profile→clean→analyze→render→report→finalize，任一段产物缺失即记 `degradations` 并把 status 降为 `partial`。
- 结果正确性（15）：数值、统计口径、图形必须算对。统计函数统一走 `stats.json`；组间比较按组数选择 Welch t / ANOVA / Mann-Whitney / Kruskal-Wallis；多重比较用 Bonferroni 并保留原始 p。
- 结果完整性（5）：题目要求的字段、检查点、产物覆盖。默认覆盖缺失、异常、重复、统计假设、图表选型、代码运行、数据库字段七类检查点。
- 结果可核验性（5）：证据链完整到可重跑。`clean_meta.consistency` 断言"删行数 == 原始行数 − 最终行数"；报告数字与 `stats.json` 同源；`sources` 每项带 locator+quote。

### 2. 使用增益（25 分）

- 质量增益（15）：前 3 分钟先落盘 partial 骨架，后续只增量改进，避免长任务中途失败留下空目录。
- 效率增益（5）：幂等阶段命令 + 自动字段/主表/分组/结局发现，减少人工 `--primary/--group/--targets` 覆盖次数。
- 增益可验证性（5）：`scripts/selftest.py` 对同一输入跑两次，断言 `stats.json` 与全部 SVG 字节一致（无随机数）。

### 3. 创新增益（15 分）

- 新增能力（5）：纯标准库实现 t/F/χ² 的 p 值（不完全 beta/gamma 连分式 + Lanczos lgamma），无 scipy 也能完成推断统计；SQLite 只读连接、表结构探测、`PRAGMA`/`SELECT` 执行。
- 方法改进（5）：字段/主表/分组/结局四类角色的自动发现与降级覆盖；清洗"标记不删除"的异常值机制避免静默丢数据。
- 创新证据（5）：`stats.json.backend.scipy` 明确记录数字由哪个后端产生；`clean_meta.consistency` 机器可断言；`degradations` 记录每处降级。

### 4. 可靠体验（10 分）

- 调用与易用性（3）：`scripts/main.py --help` 退出码 0；`run` 一键串通。
- 运行稳定性（4）：多数据形态（CSV/TSV/JSON/SQLite）、多规模（行数无上限、单文件 2MB 上限）、重复运行稳定。
- 异常处理（3）：空表、列缺失、类型不符、除零、编码错误、数据库连接失败、权限不足都走降级路径，退出码 0 并写 errors/degradations。

### 5. 生态贡献（10 分）

- 文档完整性（3）：SKILL.md 写清用途、输入输出契约、预算、自检、边界；references 补方法口径。
- 资源可复用性（4）：仅标准库依赖，脚本可被他人直接部署复跑；字段字典由 `profile.json` 自动生成。
- 分享推广价值（3）：统计选型规则沉淀在 `references/statistical_decisions.md`，可迁移到其他表格分析任务。

## 与自检项的对应（SKILL.md §8）

- S1 → 使用增益（骨架早落盘）
- S2 S3 S6 S7 → 实际效果（契约完整、状态诚实、图表同源、报告可回算）
- S4 → 实际效果 + 创新（清洗一致、标记不删除）
- S5 → 实际效果 + 创新（统计口径、后端记录）
- S8 S10 → 可靠体验（异常处置、不编造）
- S9 → 使用增益 + 创新（确定性）
- S11 → 生态贡献（资源可复跑）
