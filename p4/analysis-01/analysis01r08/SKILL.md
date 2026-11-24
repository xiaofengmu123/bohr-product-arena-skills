---
name: sci-data-cleaning
description: 对科研汇总表格做可复核、可复现的清洗：先读数据字典、每指标单位/QC规则与清洗工单，再区分未检测/低于检出限/录入缺失/真值0，处理异常值、重复记录、编码与单位转换，产出与结果表数字逐字段一致的质量报告和字段级决策台账，回填 result.json。用于科研数据清洗、数据质检与可复现流水线；输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 1.1.0
---

# 科研数据清洗 Skill（sci-data-cleaning，可核验版）

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可调用、可复核、可复跑的流程。适用：输入为已发表聚合表转录件（CSV/TSV 等），附数据字典、每指标单位/QC 规则和清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、图表出版级排版、数据库运维、实验方案设计、论文写作与投稿。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每个结论必须能指回材料逐字片段（locator + quote），每个处理动作必须落到 `rules.json` 的一条规则并进 `change_log`。

本版与通用清洗脚本的区别：把题目 5 个评分维度逐条映射到流程步骤与自检项（见 `references/scoring-map.md`），并以"字段级决策台账 `rules.json` + 单元级审计 `cell_audit.csv`"把"做了什么、为什么、依据在哪"变成机器可复核记录，而非自然语言描述。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。**output_contract 是动态的，必须原样回填全部键**：缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用 `scripts/main.py` 自动发现，**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台解析工具读取原文，不按标签臆测内容。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note` + `role`）；
5. `uncertainty`（列表，逐条写结论不确定性与依据）；
6. `change_log`（列表，每项 `time/step/action`，记录每一步改了什么、为什么、对应 `rule_id`）。

另附 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物路径）。主产物"可打开、可编辑、可复现"：清洗表给 CSV、报告给 Markdown、规则给 JSON。`partial` 结果必须标明局限。

本版新增两个可复核产物：`rules.json`（外置字段级规则，逐条 `id/字段/动作/依据/locator`）与 `cell_audit.csv`（单元级审计：`row_id/field/raw_literal/category/action/rule_id`），二者使"每个改动的依据"可逐行核对。

## 4. 执行流程与预算（总 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `--phase bootstrap`，**先落盘 result.json 骨架**（status=partial），之后只做增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 读数据字典→字段语义/量纲；读单位/QC 规则；读工单。运行 `--phase full` 生成原始快照 + 字段画像 + 默认分类 + rules.json 模板 | 材料 → raw_snapshot + field_inventory + rules.json 模板 |
| 2 | 6–12 分钟 | ≤30 | 逐字段核对并回填 rules.json 与 evidence_map.json，重跑 `--phase full` 生成 cleaned_data.csv + cell_audit.csv + data_profile.json + quality_report.md | 规则 → 修正后的清洗产物 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human/change_log；重跑 `--phase full` 定稿 | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `--phase selfcheck`，报告数字与清洗表不一致则回 Step 2 修正 | result.json → 自检结论 |

每步先写盘再分析，任何中途异常都不留不完整产物。同一输入重复运行，数据结果一致（脚本无随机、无网络；时间戳仅作 `runtime` 元数据）。

## 5. 评分维度→流程映射（本版重点）

| 评分点 | 分值 | 流程环节 | 自检项 | 关键产物 |
|---|---|---|---|---|
| 任务完成度 | 15 | Step 1–2 读全材料、覆盖五类处理 | SC1 | cleaned_data.csv + quality_report.md |
| 结果正确性 | 15 | Step 2 逐字段核对口径 | SC2 | rules.json + cell_audit.csv |
| 结果完整性 | 5 | Step 3 回填契约 | SC3 | result.json |
| 结果可核验性 | 5 | Step 4 重算一致性 | SC4 | data_profile.json + evidence_map.json |
| 质量增益 | 15 | 全程用决策台账替代通用脚本 | SC5 | rules.json |
| 效率增益 | 5 | 脚本一次跑通、零人工改表 | SC6 | scripts/main.py |
| 增益可验证性 | 5 | 确定性自检 | SC7 | 哈希/两次运行一致 |
| 新增能力 | 5 | 由字典推断字段级策略 | SC8 | field_inventory |
| 方法改进 | 5 | 每动作带 rule_id + quote | SC9 | cell_audit.csv |
| 创新证据 | 5 | 冲突并列标注 | SC10 | evidence_map.json |
| 调用与易用性 | 3 | 相对路径 + 默认参数 | SC11 | SKILL.md |
| 运行稳定性 | 4 | 退出码 0 + 确定性 | SC12 | scripts/main.py |
| 异常处理 | 3 | 降级策略 | SC13 | errors/degradations |
| 文档完整性 | 3 | frontmatter + 边界 | SC14 | SKILL.md |
| 资源可复用性 | 4 | 规则外置可读配置 | SC15 | rules.json |
| 分享推广价值 | 3 | 最小调用示例（标示例） | SC16 | references/ |

完整逐条映射见 `references/scoring-map.md`，可勾选自检见 `references/checklist.md`。

## 6. 清洗规则（保守默认，逐字段写依据）

- **缺失值**：区分 `未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。四类保留原文并分类，**不互相覆盖**；默认不插补，确需标记时写 change_log。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准判断，保留/剔除/盖帽均写理由与 locator；不用"均值±n 倍标准差"武断删除真实高值。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致的近似重复单独报告；明确保留哪一条及依据。
- **格式/单位**：编码先于解析（自动容错 UTF-8/GBK/BOM）；科学计数法、千分位、有效数字不做静默截断；单位换算保留映射表与换算因子；摩尔浓度等需摩尔质量的单位**不猜换算**，标 `pending_human`。
- **质量报告**：字段级清单（缺失率、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字必须由清洗后表格实时重算**，不得手写。

## 7. 自检清单（对应评分维度，逐条可勾选）

- [ ] SC1 case.json 与 task.md 已读，output_contract 全部键已回填（null/[] 不丢键）——结果完整性/可复核性。
- [ ] SC2 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] SC3 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——实际效果/正确性。
- [ ] SC4 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] SC5 重复识别基于真实主键，保留策略明确——正确性。
- [ ] SC6 单位/编码转换可逆或留映射表，精度未丢失——正确性。
- [ ] SC7 报告数字与 cleaned_data.csv 逐字段一致（可重算核对）——可复核性。
- [ ] SC8 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 source_conflict——证据溯源。
- [ ] SC9 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃（退出码 0）——可靠体验。
- [ ] SC10 两次运行数据结果一致（确定性）；partial 结果标注局限——可靠体验。
- [ ] SC11 每个处理动作在 rules.json 有 rule_id，在 cell_audit.csv 有对应行——可复核性/创新增益。
- [ ] SC12 result.json 经 `--phase selfcheck` 通过——交付。

## 8. 降级策略（缺数据/缺软件时）

- 缺材料：status=`insufficient_evidence`，在 `pending_human` 列缺口，**不编造数据或结论**。
- 缺软件（渲染/OCR 失败）：status 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因，保留已完成合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不做静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃。

## 9. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --output /app/output
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定）仍由 Agent 完成，并写入 rules.json 与 evidence_map.json。不得用脚本输出冒充人工判断，不得预写正式答案。
