---
name: sci-table-clean
description: 对已发表科研聚合表转录件做可复核清洗：先读数据字典、单位/QC规则与清洗工单，把字段级规则外置为 rules.json，区分未检测/低于检出限/录入缺失/真值0，处理异常值、重复记录、编码与单位转换，生成与清洗表数字一致的质量报告与 result.json。用于科研数据清洗、数据质检与可复现流水线场景，当输入是带编码/存储故障的聚合表转录件时使用。
version: 1.0.0
---

# sci-table-clean：可复核的科研聚合表清洗

## 1. 角色与何时使用

本 Skill 把"科研聚合表清洗"沉淀为可复跑流程。适用：已发表聚合表转录件（CSV/TSV 等）加数据字典、单位/QC 规则、清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、出版级排版、数据库运维。

红线：**不插补缺失/无效源值，不推断总体统计，不静默改值/删行/换单位，不编造数据、分数或引用**。每个关键结论必须能指回材料的逐字片段（locator + quote）。

## 2. 输入契约

- `/input/case.json`：`case_id`、`output_contract`（动态字段，**必须原样回填全部键**，缺值给 `null`/`[]`，不改名不删键）、材料清单。
- `/input/task.md`：任务与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用 `scripts/main.py` 自动发现并**逐份打开核对内容**，不按文件名臆测。
- PDF/图片用平台解析工具读原文，不按标签猜内容。
- 中间文件在 `/app/work/`，最终产物在 `/app/output/`。

## 3. 输出契约

`/app/output/result.json` 必须含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`：每项 `locator` + 逐字 `quote` + `note`；
5. `uncertainty`：逐条写结论的不确定性与依据；
6. `change_log`：每项 `time/step/action`，记录每一步改了什么、为什么；
7. `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物路径）。

主产物可打开可复现：清洗表给 CSV，报告给 Markdown，规则给 JSON，证据映射给 JSON。partial 结果必须标局限。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求 | 动作 |
|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，跑 `--phase bootstrap`，**先落盘 result.json 骨架（status=partial）**，再增量改进 |
| 1 | 3–6 分钟 | ≤15 | 读数据字典→字段语义/量纲；读单位/QC 规则；读工单；把结论写成 `/app/work/rules.json` |
| 2 | 6–12 分钟 | ≤30 | 跑 `--phase full`：清洗表 + 数据画像 + 质量报告 + 证据映射；逐字段核对缺失分类/异常上下界/重复主键/单位换算/报告数字 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human；写 change_log |
| 4 | 最后 1 分钟 | ≤5 | 跑 `--phase selfcheck`，通过后结束 |

每步先写盘再分析。同一输入重复运行结果一致（脚本无随机、无网络；时间戳仅作元数据）。

## 5. 清洗规则（保守默认，逐字段写依据）

详见 `references/rules.md`。要点：缺失四分类不互覆盖；异常值以物理上下界为准且写理由；去重按真实主键并处理近似重复；单位/编码转换留映射表、摩尔质量不明不猜；质量报告数字由清洗表实时重算。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] case.json/task.md 已读，output_contract 全部键回填——结果完整性。
- [ ] 字典/QC 规则/工单均打开并按内容执行——实际效果。
- [ ] 未检测/低于检出限/录入缺失/真值0 四类分开，无插补——正确性。
- [ ] 异常值有物理上下界与 locator 依据——正确性。
- [ ] 去重基于真实主键，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（可重算）——可复核性。
- [ ] 关键结论有 locator + 逐字 quote；冲突并列标 source_conflict——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并记录，退出码 0——可靠体验。
- [ ] 两次运行结果一致；partial 标局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 通过——交付。

## 7. 降级策略

- 缺材料：`status=insufficient_evidence`，`pending_human` 列缺口，不编造。
- 缺软件/解析失败：`status` 最多 `partial`，`degradations`/`errors` 写失败步骤与原因，保留已完成合法产物。
- 单位不明/规则冲突/主键冲突：标 `uncertainty` 或 `pending_human`，不静默处理。
- 写盘失败：降级写最小合法 result.json，不让进程崩溃。

## 8. 脚本调用（相对路径，标准库）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python scripts/selftest.py               # 交付前自检（空输入/坏JSON/规则应用/一致性）
```

脚本是确定性辅助工具；字典解读、规则冲突裁决、主键判定由 Agent 完成并写入 change_log 与 evidence_map。不得用脚本输出冒充人工判断，不得预写正式答案。
