---
name: sci-data-cleaning
description: 对科研汇总表格做可复核清洗：先读数据字典、每指标单位/QC规则与清洗工单，再把每个字段的处置决策固化为结构化中间产物 plan.json，据此执行保守清洗（区分未检测/低于检出限/录入缺失/真值0，不插补、不推断总体），产出 field_model.json 与逐字段质量报告，并用 selfcheck 从清洗表反向重算核对报告数字，最终写回 result.json。用于科研数据清洗、数据质检与可复现流水线场景，当输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 1.1.0
---

# 科研数据清洗 Skill（sci-data-cleaning）

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可调用、可复核的流水线。适用：输入为已发表聚合表转录件（CSV/TSV 等），附数据字典、每指标单位/QC 规则和清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、图表出版级排版、数据库运维。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每个关键结论必须能指回材料的逐字片段（locator + quote）。

本版的设计取舍是**中间表示与可复核**：先产出机器可读的 `plan.json`（每字段处置决策 + 逐字依据），再执行，再用 `field_model.json` 记录清洗后每字段统计，最后用 `selfcheck` 从清洗表反向重算，证明报告数字与结果表一致。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。**output_contract 字段是动态的，必须原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用 `scripts/main.py` 自动发现，**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台提供的解析工具读原文，不按标签臆测内容；读到的文字再纳入证据。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（逐条写结论的不确定性与依据）；
6. `change_log`（每项 `time/step/action`，记录每一步改了什么、为什么）。

另加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`。主产物可打开、可编辑、可复现：

- `cleaned_data.csv`（清洗表，主产物）
- `raw_snapshot.csv`（编码纠正后的原始快照，未改动）
- `plan.json`（结构化中间表示：每字段决策 + locator 依据）
- `field_model.json`（清洗后每字段统计，由清洗表重算）
- `quality_report.md`（字段级质量报告，数字与上表一致）
- `evidence_map.json`（动作 → locator + quote 证据链）
- `removed_duplicates.csv`（如有重复被剔除）

**partial 结果必须标明其局限。**

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work`，**先落盘 result.json 骨架（status=partial）**，再允许增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 逐份读数据字典→字段语义/量纲；读单位/QC 规则；读工单。把解读**写成 plan.json**（不要只写报告）；运行 `--phase full` 生成清洗表 + field_model + 报告 + 证据 | 材料 → plan.json + cleaned_data.csv + field_model.json + quality_report.md + evidence_map.json |
| 2 | 6–12 分钟 | ≤30 | 逐字段核对：缺失分类、异常判据（对照 QC 上下界与物理范围）、重复主键、单位转换映射、报告数字与清洗表一致；有冲突就并列写 `source_conflict` 到 evidence_map | 中间产物 → 修正后的 plan.json/结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human；写 change_log | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `python scripts/main.py --phase selfcheck --output /app/output`，确认合法后结束 | result.json → selfcheck.json |

每步先写盘再分析，中途异常不留不完整产物。同一输入重复运行，数据结果必须一致（脚本无随机、无网络；时间戳仅作元数据）。**前 3 分钟必须先落盘 partial 骨架，之后只做增量改进。**

## 5. 清洗规则（保守默认，逐字段写依据）

- **缺失值**：四类分开——`未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。保留原文并分类，**不互相覆盖、不插补**。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准判断；保留/剔除/盖帽均写理由与 locator；不用"均值±n 倍标准差"武断删除真实高值。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致的近似重复单独报告；明确保留哪一条及依据。
- **格式/单位**：编码先于解析（脚本自动容错 UTF-8/GBK/BOM）；科学计数法、千分位、有效数字不做静默截断；单位换算保留映射表与换算因子；摩尔浓度等需摩尔质量的换算**不猜**，标 `pending_human`。
- **中间表示**：`plan.json` 是决策层，`field_model.json` 是结果层，二者 + 清洗表三向核对；规则外置在 plan.json 中，便于他人复跑与增量更新。
- **质量报告**：字段级清单（缺失率、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字必须由清洗后表格实时重算，不得手写。**

## 6. 自检清单（对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性/可复核性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——正确性。
- [ ] 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] 重复识别基于真实主键，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失；摩尔换算标 pending_human——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（可重算核对）——可复核性。
- [ ] plan.json / field_model.json / 清洗表三向一致——中间表示与可复核。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 source_conflict——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃（退出码 0）——可靠体验。
- [ ] 两次运行数据结果一致（确定性）；partial 结果标注局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 通过；`scripts/selftest.py` 三场景 PASS——交付。

## 7. 降级策略（缺数据/缺软件时）

- 缺材料：status=`insufficient_evidence`，在 `pending_human` 列缺口，**不编造数据或结论**。
- 缺软件（如渲染/OCR 失败）：status 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不做静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃。

## 8. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python scripts/selftest.py               # 三场景自检，退出码 0 表示 PASS
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定）仍由 Agent 完成，并写入 `plan.json` 与 `evidence_map.json`。不得用脚本输出冒充人工判断，不得预写正式答案。
