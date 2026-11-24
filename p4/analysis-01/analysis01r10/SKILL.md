---
name: sci-cleaning-pipeline
description: 对科研聚合表转录件做证据可溯、规则外置、可复跑复算的清洗：先读数据字典、单位/QC 规则与清洗工单，把每格建成带来源定位的中间表示，再区分未检测/低于检出限/录入缺失/真值0，处理异常值、重复记录、编码与单位转换，由清洗表实时重算质量报告，产出 result.json。用于科研数据清洗、数据质检与可复现流水线场景，输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 1.0.0
---

# 科研数据清洗 Skill（sci-cleaning-pipeline）

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可调用、可复核、可复跑的流程。适用：输入为已发表聚合表转录件（CSV/TSV/TXT 等），附数据字典、每指标单位/QC 规则与显式清洗工单，可能带编码/存储故障（GBK 乱码、BOM、哨兵值、科学计数法、千分位、近似重复）。不适用：原始实验数据的统计建模与假设检验、图表出版级排版、数据库运维与权限治理。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每个关键结论必须能指回材料的逐字片段（locator + quote）。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。**output_contract 字段是动态的，必须原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，由脚本自动发现；**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台提供的解析工具读取原文，不要按标签臆测内容。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填到顶层，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（列表，逐条写结论的不确定性与依据）；
6. `change_log`（列表，每项 `time/step/action`，记录每一步改了什么、为什么）。

建议再加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物相对路径）。主产物要"可打开、可编辑、可复现"：清洗表给 CSV，报告给 Markdown，规则配置给 JSON，证据映射给 JSON。**partial 结果必须标明其局限。**

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `--phase bootstrap`，**先落盘 result.json 骨架**（status=partial），之后只做增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 读数据字典→字段语义/量纲；读单位/QC 规则；读工单。运行 `--phase full` 生成清洗表 + 数据画像 + 质量报告 + 证据映射 + 规则解析 | 材料 → cleaned_data.csv + data_profile.json + quality_report.md + evidence_map.json + rules_resolved.json |
| 2 | 6–12 分钟 | ≤30 | 逐字段核对：缺失分类、异常判断（对照 QC 上下界与物理范围）、重复主键、单位映射、报告数字与清洗表一致性 | 中间产物 → 修正后的结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human；写 change_log | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `--phase selfcheck`，由独立校验器复算确认后结束 | result.json → 自检结论 |

每步先写盘再分析：任何中途异常都不留不完整产物。同一输入重复运行，数据结果必须一致（脚本无随机、无网络；时间戳仅作元数据）。**校验器与执行器分离**：`selftest.py` 独立复算报告数字，不信任生成器手写的数字。

## 5. 清洗规则（保守默认，逐字段写依据，详见 references/rules.md）

- **缺失值**：四类分开——`未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。保留原文并分类，**不互相覆盖**；默认不插补。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界判断，保留/剔除/截帽均写理由与 locator；不用"均值±n 倍标准差"武断删除真实高值。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致的近似重复单独报告；明确保留哪一条及依据。
- **格式/单位**：编码先于解析（脚本容错 UTF-8/GBK/BOM）；科学计数法、千分位、有效数字不做静默截断；单位换算保留映射表与换算因子；摩尔浓度等需摩尔质量的单位**不猜换算**，标 `pending_human`。
- **质量报告**：字段级清单（缺失率、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字必须由清洗后表格实时重算**，不得手写。

## 6. 自检清单（对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性/可复核性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——实际效果/正确性。
- [ ] 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] 重复识别基于真实主键，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（可重算核对）——可复核性。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 source_conflict——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃（退出码 0）——可靠体验。
- [ ] 两次运行数据结果一致（确定性）；partial 结果标注局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 通过——交付。

## 7. 降级策略（缺数据/缺软件时）

- 缺材料：status=`insufficient_evidence`，在 `pending_human` 列缺口，**不编造数据或结论**。
- 缺软件（渲染/OCR 失败）：status 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不做静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃。

## 8. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定）仍由 Agent 完成，并写入 change_log 与 evidence_map。不得用脚本输出冒充人工判断，不得预写正式答案。

## 9. 最小调用示例（示意，非正式案例结果）

```text
输入 /input/case.json 的 output_contract 为 {"clean_table": "...", "quality_report": "..."}：
  bootstrap → /app/output/result.json（status=partial，clean_table=null，quality_report=null）
  full      → /app/output/cleaned_data.csv + data_profile.json + quality_report.md + evidence_map.json
  selfcheck → 校验器复算报告数字，向 result.json 追加 selfcheck 结论
```

## 10. 差异化设计（相对参考打法的取舍）

- **执行器与校验器分离**：`core_impl.py` 负责生成，`selftest.py` 独立复算报告数字与契约完整性，自检不信任生成器。
- **中间表示**：每个单元格建成 `{raw, display, missing_class, unit_hint, locator}`，缺失分类/异常/单位转换都作用于此 IR，便于逐格回查。
- **规则外置**：字典/QC 规则先解析成 `rules_resolved.json` 的可读配置，清洗按该配置执行，Agent 可先审后改再重跑。
- **校验顺序**：先落盘骨架 → 生成产物 → 独立复算校验，任何阶段失败都保留上一版合法结果。
