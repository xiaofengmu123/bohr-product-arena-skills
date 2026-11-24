---
name: sci-repro-clean
description: 对科研汇总表格做可复现、可复核的确定性清洗：先读数据字典、逐字段单位/QC规则与清洗工单，再把缺失值区分为未检测/低于检出限/录入缺失/真值0，处理异常值、重复记录、编码与单位换算，输出与结果表逐字段一致的质量报告与 result.json；内置内容指纹与 selftest.py 自检，保证同一输入两次产物一致。用于科研数据清洗、数据质检与可复现流水线，当输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 2.0.0
---

# 科研数据清洗 Skill（sci-repro-clean，自检与确定性版）

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可调用、可复核、可复跑的流程。适用：输入为已发表聚合表转录件（CSV/TSV 等），附数据字典、逐指标单位/QC 规则与清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、图表出版级排版、数据库运维与权限治理。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每个关键结论必须能指回材料逐字片段（locator + quote）。

本版相对通用流程的差异设计（变体取舍）：
- **确定性优先**：脚本无随机、无网络；输出按固定顺序（排序键、原行序）；内容指纹 `canonical_fingerprint` 只忽略 `runtime`/时间戳等易变元数据，两次运行可字节级比对。
- **自检内置**：`scripts/selftest.py` 三模式（package / result / determinism），把"报告数字与清洗表一致"变成可重跑的机械复核，而非口头承诺。
- **证据自动落盘**：脚本从材料原文自动抽取 locator + 逐字 quote 写入 evidence_map.json，减少 Agent 手抄、防伪造。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。**output_contract 字段是动态的，必须原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用 `scripts/main.py` 自动发现，**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：先调用平台解析工具，把提取文本放到 `/app/work/extracted/<原名>.txt`，再重跑 `--phase full`；脚本不臆测原文。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键；`null`/`[]` 不丢键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（逐条写结论的不确定性与依据）；
6. `change_log`（每项 `step/action/detail/time`，记录每一步改了什么、为什么）。

再加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物相对路径）。主产物"可打开、可编辑、可复现"：`cleaned_data.csv`（保留原值 + 分类 + 数值列 + 单位列 + 标记列）、`data_profile.json`、`quality_report.md`、`evidence_map.json`、`run_manifest.json`（输入输出内容哈希）。**partial 结果必须标明局限。**

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work`，**先落盘 result.json 骨架**（status=partial） | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 非文本材料先文本化到 `/app/work/extracted/`；读数据字典→字段语义/量纲；读单位/QC 规则；读工单。运行 `--phase full` | 材料 → cleaned_data.csv + data_profile.json + quality_report.md + evidence_map.json + run_manifest.json |
| 2 | 6–12 分钟 | ≤30 | 逐字段核对：缺失四类、异常判据（对照 QC 上下界与物理范围）、重复主键、单位换算映射、报告数字与清洗表一致性；修正 result.json 中 sources/uncertainty/pending_human/change_log | 中间产物 → 修正后的结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 uncertainty/pending_human；写 change_log；**冻结内容指纹** | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `python scripts/selftest.py --mode result --input /input --output /app/output --work /app/work`，再跑 `--mode determinism`，确认合法后结束 | result.json → 自检结论 |

每步先写盘再分析：任何中途异常都不留不完整产物。同一输入重复运行，数据结果必须一致（脚本无随机、无网络；时间戳仅作元数据，排除在指纹外）。

## 5. 清洗规则（保守默认，逐字段写依据）

- **缺失值**：区分 `未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。四类保留原文并分类，**不互相覆盖**。默认不插补；确需标记时写入 change_log。列 `<col>__class` 记录分类，`<col>__numeric` 记录可解析数值，原值列保持逐字原文。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准打标（`outlier_low/high`），默认**保留**；剔除/截帽只在工单明确要求时执行并写理由与 locator；不用"均值±n 倍标准差"武断删除真实高值。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致经 NFKC + 空白折叠归一后归为近似重复；明确保留首条（原行序确定）并记录重复组。
- **格式/单位**：编码先于解析（自动容错 UTF-8/GBK/BOM，并启发式修复 UTF-8 双编码乱码）；科学计数法、千分位、有效数字不做静默截断（用 `Decimal` 保精度）；单位换算保留映射表与因子；摩尔浓度等需摩尔质量的单位**不猜换算**，标 `pending_human`。
- **质量报告**：字段级清单（四类缺失计数、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字必须由清洗后表格实时重算**，自检脚本再解析 CSV 复核，不得手写。

## 6. 自检清单（对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性/可复核性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失四类（未检测/低于检出限/录入缺失/真值0）分开，无插补、无静默改值——实际效果/正确性。
- [ ] 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] 重复识别基于真实主键，近似重复已归一，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失，摩尔浓度不猜换算——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（`selftest.py --mode result` 复核通过）——可复核性。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 `source_conflict`——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃（退出码 0）——可靠体验。
- [ ] 两次运行内容指纹一致（`selftest.py --mode determinism` 通过）；partial 结果标注局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 通过；交付前 `selftest.py --mode package` 通过——交付。

## 7. 降级策略（缺数据/缺软件时如实标注，不编造）

- 缺材料：status=`insufficient_evidence`，在 `pending_human` 列缺口，**不编造数据或结论**。
- 缺软件（如 PDF 渲染/OCR 失败）：status 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、摩尔质量缺失：标 `uncertainty` 或 `pending_human`，不做静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃。

## 8. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap   --input /input --output /app/output --work /app/work
python scripts/main.py --phase full        --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck   --input /input --output /app/output --work /app/work
python scripts/selftest.py --mode package     --skill-root /skills/weekly-skill
python scripts/selftest.py --mode result      --input /input --output /app/output --work /app/work
python scripts/selftest.py --mode determinism --input /input --work /app/work
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定）仍由 Agent 完成，写入 change_log 与 evidence_map。不得用脚本输出冒充人工判断，不得预写正式答案。

## 9. 确定性与证据（本版核心保证）

- **确定性**：文件遍历排序、JSON `sort_keys`、CSV 保原行序、去重保首条、`Decimal` 保精度；唯一易变项是 `runtime`/`time`，已排除在内容指纹外。
- **可复核**：`run_manifest.json` 记录每个输入/输出的 SHA-256，任何改动可被察觉。
- **证据溯源**：`evidence_map.json` 与 `sources` 每项均含 `locator`（`file#L<行号>`）+ 逐字 `quote`；找不到依据的结论标 `pending_human`，不杜撰。

参考 `references/rules.md`（规则细节）与 `references/checklist.md`（交付前勾选单）。
