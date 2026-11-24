---
name: sci-data-cleaning
description: 对科研汇总表做可复核清洗：先读数据字典、每指标单位/QC规则与清洗工单，再区分未检测/低于检出限/录入缺失/真值0，处理异常值、重复记录、编码与单位转换，生成与结果表数字一致的字段级质量报告，并把每条结论绑定 locator+逐字 quote 的细粒度 evidence_map，产出 result.json。用于科研数据清洗、数据质检与可复现流水线，当输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 2.0.0
---

# 科研数据清洗 Skill（sci-data-cleaning）

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可调用、可复核、可增量更新的流程。适用：输入为已发表聚合表转录件（CSV/TSV/TXT 等），附数据字典、每指标单位/QC 规则和清洗工单，且可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、出版级图表排版、数据库运维、实验方案设计与论文写作。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每条关键结论必须能指回材料中的逐字片段（locator + quote）。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段，动态）、材料清单与证据范围。**output_contract 的全部键必须原样回填到 result.json 顶层**，缺值给 `null`/`[]`/`{}`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用 `scripts/main.py` 自动发现；**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台提供的解析工具读原文，不按标签臆测。

## 3. 输出契约（`/app/output/result.json`）

必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填到顶层，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（列表，逐条写结论的不确定性与依据）；
6. `change_log`（列表，每项 `time/step/action`，记录每一步改了什么、为什么）。

再加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物路径）、`_meta`（内部审计：`contract_keys`、`input_dir`、`schema_version`）。主产物要"可打开、可编辑、可复现"：清洗表给 CSV，报告给 Markdown，证据给 `evidence_map.json`，原始快照给 `source_snapshot.json`。**partial 结果必须标明局限。**

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work`，**先落盘 result.json 骨架（status=partial）**，之后只做增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 逐份读数据字典→字段语义/量纲；读单位/QC 规则；读工单。把语义判断写成 `/app/work/agent_rules.json`（见第 9 节） | 材料 → agent_rules.json |
| 2 | 6–12 分钟 | ≤30 | 运行 `--phase full` 生成清洗表 + 数据画像 + 质量报告 + 证据映射；逐字段核对缺失分类、异常判据（对照 QC 上下界与物理范围）、重复主键、单位换算映射、报告数字与清洗表一致性 | 中间产物 → 修正后结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human；写 change_log | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work`，确认合法后结束 | result.json → 自检结论 |

每步先写盘再分析；任何中途异常都不留不完整产物。同一输入重复运行，数据结果必须一致（脚本无随机、无网络；时间戳仅作元数据）。

## 5. 清洗规则（保守默认，逐字段写依据）

- **缺失值**：区分 `未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。四类保留原文并分类，**不互相覆盖、不插补**；确需标记时在 change_log 记录。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准判断，保留/剔除/截帽均写理由与 locator；不用"均值±n 倍标准差"武断删除真实高值。脚本的 IQR 标记仅作诊断提示，默认 `action=keep`。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致的近似重复单独报告；明确保留哪一条及依据。
- **格式/单位**：编码先于解析（自动容错 UTF-8/GBK/BOM）；科学计数法、千分位、有效数字不做静默截断；单位换算保留映射表与换算因子，原列保留、新增 `__conv_*` 列；摩尔浓度等需摩尔质量的单位**不猜换算**，标 `pending_human`。
- **质量报告**：字段级清单（缺失率、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字由 `data_profile.json` 生成，并由 selfcheck 从 cleaned_data.csv 实时重算**，不得手写。

## 6. 证据溯源强化（本版重点）

- **每条结论带 locator + 逐字 quote**：`evidence_map.json` 采用细粒度结构——`assertions[]` 每项含 `id / kind / field / record / assertion / evidence{locator,quote,material,material_sha256} / rule_ref / basis[] / confidence / uncertainty / change_log_id`，另有 `field_index` 与 `counts`。
- **locator 语法**：`材料名::r行号::c[列名]`（单元格）、`材料名::field::列名`（字段级）、`材料名::duplicate-group::键`、`材料名::line行号`（规则/字典）。行列均为 1 基。
- **quote 必须逐字**：selfcheck 会重新打开被引材料，逐条做子串核验（仅归一化换行/首尾空白），核不过的断言记入 `degradations`，不冒充已核验。
- **报告数字可追溯**：质量报告中的每个数字由 `data_profile.json` 单一来源生成，selfcheck 用同一套 `classify_value` 从 CSV 重算并逐项比对（`n_rows / n_cols / missing_tallies / n_outlier_flags / n_duplicate_*`）。
- **冲突并列**：同一指标两个来源不一致时，两条都记录并标 `source_conflict`，不静默取一。

## 7. 自检清单（对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性/可复核性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——实际效果/正确性。
- [ ] 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] 重复识别基于真实主键，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（`--phase selfcheck` 重算核对）——可复核性。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 source_conflict——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃（退出码 0）——可靠体验。
- [ ] 两次运行数据结果一致（确定性）；partial 结果标注局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 通过；`python scripts/selftest.py` 全绿——交付。

## 8. 降级策略（缺数据/缺软件时）

- 缺材料：status=`insufficient_evidence`，在 `pending_human` 列缺口，**不编造数据或结论**。
- 缺软件（解析/渲染/OCR 失败）：status 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不做静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃。
- 文件超 2MB、坏 JSON、坏编码：脚本捕获并记录到 `errors`/`degradations`，继续处理其余材料。

## 9. 脚本调用与 agent_rules

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python scripts/selftest.py   # 本地自检（合成夹具，非正式答案）
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定、异常处置）仍由 Agent 完成，写成 `/app/work/agent_rules.json`：

```json
{
  "primary_keys": ["sample_id"],
  "qc_bounds": {"conc_mgL": {"min": 0, "max": 100, "action": "keep"}},
  "unit_conversions": [{"field": "conc_mgL", "from_unit": "mg/L", "to_unit": "ug/L",
                        "factor": 1000.0, "material": "qc_rules.txt",
                        "locator": "qc_rules.txt::line1", "quote": "mg/L 转 ug/L 乘 1000"}],
  "field_semantics": {"conc_mgL": {"meaning": "浓度（mg/L）", "material": "dictionary_fields.csv",
                                   "locator": "dictionary_fields.csv::r2", "quote": "conc_mgL,浓度(mg/L)"}},
  "pending_human": []
}
```

`material/locator/quote` 必须指向同一来源的逐字片段。不得用脚本输出冒充人工判断，不得预写正式答案。详见 `references/cleaning-rules.md` 与 `references/evidence-schema.md`。
