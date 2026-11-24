# 评分维度 → 流程阶段 → 自检项 映射表

本文件是 SKILL.md 第 5 节的执行细则：把题目五个评分维度逐条映射到
具体阶段动作与可勾选的自检项，使「评分想查什么」与「流程实际做什么」
一一对应。复核人按本表即可定位某条评分对应的产物与位置。

| 评分维度（分值） | 评分点 | 本 Skill 的阶段动作 | 产物字段 | 对应自检项 |
|---|---|---|---|---|
| 实际效果 40 | 任务完成度 15 | 阶段 0–3 全链路：盘点、组织、问答、回链、记录 | `library`、`answers`、`change_log` | 完整性、契约回填 |
| 实际效果 40 | 结果正确性 15 | 阶段 2 逐问检索并摘录逐字 quote；版本/权限按哈希与 mode 处置 | `answers[].evidence[]`、`sources[]` | 正确性 |
| 实际效果 40 | 结果完整性 5 | 阶段 1 `inventory` 覆盖六维度 | `library.file_types/directory_tree/duplicate_groups/version_families/permissions_and_source/metadata_fields` | 完整性 |
| 实际效果 40 | 结果可核验性 5 | 哈希 + 版本记录 + locator + quote，可复跑 | `sources[].sha256`、`evidence_map.json`、`operation_evidence` | 可核验性 |
| 使用增益 25 | 质量增益 15 | 确定性 evidence 索引约束 Agent 只引用真实片段，减少幻觉 | `evidence_index.json`、`answers[].evidence[]` | 正确性、缺证判据 |
| 使用增益 25 | 效率增益 5 | 一次 `inventory` 完成全量扫描，避免逐文件重复检索 | `inventory.json`、`library` | 可复现 |
| 使用增益 25 | 增益可验证性 5 | 每步 `change_log` + 时间戳，可对照基础 Agent 复现 | `change_log[]`、`runtime` | 可核验性、可复现 |
| 创新增益 15 | 新增能力 5 | 缺证判据（敢于说「没有」）与冲突聚合 | `uncertainty[]`、`answers[].missing` | 缺证判据、冲突聚合 |
| 创新增益 15 | 方法改进 5 | 内容哈希去重、版本基名分组、PDF 文本层探测 | `duplicate_groups[]`、`version_families[]`、`files[].pdf_text_layer` | 正确性、完整性 |
| 创新增益 15 | 创新证据 5 | `selftest.py` 在临时目录复现降级路径，证明边界与价值 | selftest 输出、`degradations[]` | 运行稳定、资源复用 |
| 可靠体验 10 | 调用与易用性 3 | 三个子命令 + `--help`，缺省路径指向 /input、/app/output、/app/work | CLI | CLI 与状态码 |
| 可靠体验 10 | 运行稳定性 4 | 不同文件类型、目录规模、重复版本下确定性运行 | `status`、`errors` | 运行稳定 |
| 可靠体验 10 | 异常处理 3 | 空目录/坏 JSON/坏编码/无权限/扫描件/冲突均识别处置 | `errors`、`degradations`、`uncertainty` | 运行稳定、不确定性 |
| 生态贡献 10 | 文档完整性 3 | SKILL.md + references 写清用途、契约、方法、边界、自查 | SKILL.md、references/ | 文档完整 |
| 生态贡献 10 | 资源可复用性 4 | 脚本只用标准库、相对路径、可独立运行 | scripts/ | 资源复用 |
| 生态贡献 10 | 分享推广价值 3 | `inventory.md` + `evidence_map.json` 作为可交接清单与证据索引 | 可读产物 | 交接价值 |

## 使用方法

1. 交付前跑 `python3 scripts/selftest.py`，确认自检项全过。
2. 对任一评分点，按上表右列「对应自检项」在 SKILL.md 第 6 节核对。
3. 对「实际效果」四子项，逐条检查 result.json 中对应的产物字段是否非空且真实。
4. 发现缺口时，优先补 `answers.json` 与 `inventory.json` 的真实证据，而不是增补文字说明。

## 相对参考打法的设计取舍

- 参考包把重心放在「多脚本流水线 + 清单产出」，本包把重心放在**评分维度逐条对齐**：每个阶段动作都能回答「对应哪一条评分、落在哪个字段」。
- 本包用单一 `main.py` 子命令 + `pipeline.py` 核心，减少脚本数量与调用歧义；`selftest.py` 直接复现降级路径，使「创新证据」和「运行稳定」可被机器核对。
- 六维度中的「权限与来源」显式区分 `task_input`（case.json/task.md）与 `materials`（研究材料），避免把任务接口文件误当研究材料统计。
