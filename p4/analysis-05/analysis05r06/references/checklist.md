# 自检与评分维度对照清单

本文件是 SKILL.md 第 5 节自检清单的展开版，逐条说明“查什么、怎么查、对应哪项评分”。提交前与每次运行后都应按此核对。

## 1. 实际效果（40 分）

| 检查点 | 判定方法 | 依据文件 |
|---|---|---|
| 五段链路串通 | `run` 或分步命令后，`profile→plan→clean→stats→figure→report` 六个中间表示全部存在 | `work/*.json` |
| 数值/口径算对 | 报告数字与 `stats.json` 逐项一致；检验方法与组数匹配（2 组用 Welch t，≥3 组用 ANOVA） | `work/stats.json`、`report.md` |
| 字段/检查点覆盖 | `contract` 键齐全且等于 `case.json` 的 `output_contract` 全部键 | `result.json` |
| 证据链可重跑 | `manifest.json` 记录每个产物的 sha256/bytes/path；两次运行字节一致 | `work/manifest.json` |

## 2. 使用增益（25 分）

- 质量增益：所有统计与图形均由确定性脚本产出，不依赖模型临场口算；模型只负责“读任务、选列、读产物”。
- 效率增益：`run` 一条命令完成全链路；分步命令可断点续跑、幂等重跑，减少重跑次数。
- 增益可验证：`scripts/selftest.py` 在空/坏/正常输入下各跑一次并输出 PASS/FAIL。

## 3. 创新增益（15 分）

本包的核心取舍是**中间表示与可复核**，与“直接出结论”的包形成差异：

- `plan.json` 是“执行计划 IR”：记录主表、分组列、目标列、每步方法选择与理由、以及 agent 的 `--primary/--group/--targets` 覆盖，使“为什么这么算”可被审查。
- `figure_model.json` 是“图形 IR”：把数据与视觉编码解耦，SVG 只由该模型渲染；审查者不看图也能核对图中每个数来自 `stats.json` 的哪一路径。
- `manifest.json` 是“产物溯源 IR”：每个产物带内容哈希，重复运行可验证确定性。

## 4. 可靠体验（10 分）

| 场景 | 期望行为 | 检查方式 |
|---|---|---|
| 空输入目录 | 退出码 0，status=insufficient_evidence，不抛异常 | `selftest` 第 6 项 |
| 坏 JSON/坏编码 | 记入 errors/degradations，继续处理其余材料 | 读 `result.json` |
| 缺列/类型不符 | 跳过该列，记 `degradations` | 读 `result.json` |
| 除零/空组 | 检验返回 `skipped`，写 `uncertainty` | 读 `stats.json` |
| 重复运行 | `stats.json` 字节一致 | `selftest` 第 5 项 |

## 5. 生态贡献（10 分）

- 文档：`SKILL.md` 写清角色、输入输出契约、执行流程、自检、降级；本文件与 `methods.md` 补充规则与方法依据。
- 复用：脚本只依赖标准库，路径相对，可直接部署到 `/skills/weekly-skill`。
- 迁移：七段流水线与五个 IR 的结构可直接复用到同类表格分析任务。
