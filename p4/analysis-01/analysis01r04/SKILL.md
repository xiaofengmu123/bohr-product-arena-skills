---
name: sci-data-cleaning
description: 对科研汇总表格做可复核、非破坏式清洗：先读数据字典、每指标单位/QC规则与清洗工单，再区分未检测/低于检出限/录入缺失/真值0，处置异常值、重复记录、编码与单位转换，生成与清洗表数字一致且带证据溯源的质量报告，并把局限与不确定性显式写进 result.json。适用于输入为带编码/存储故障的已发表聚合表转录件、需要逐字段可复核的数据质检场景。
version: 1.0.0
---

# 科研数据清洗 Skill（sci-data-cleaning）

## 1. 角色与何时使用

本 Skill 把“科研聚合表清洗”沉淀为可复核、非破坏式的流程。适用：输入是已发表聚合表
转录件（CSV/TSV 等），附数据字典、每指标单位/QC 规则与清洗工单，可能带编码/存储故障。
不适用：原始实验数据的统计建模与假设检验、出版级排版、数据库运维。

本版设计取舍（区别于通用清洗包）：**降级与不确定性显式化**。任何处置要么有逐字证据，
要么显式写 `pending_human`/`uncertainty`/`limitations`，缺数据绝不编造；清洗采用
“注释优先、原文保留”的非破坏式流水线，每个变更可逆、可审计。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）。**output_contract
  是动态的，必须原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用
  `scripts/main.py` 自动发现，**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台解析工具读原文，不按标签臆测内容。

## 3. 输出契约

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（每项 `claim` + `basis` + `impact`，写结论的不确定性与依据）；
6. `limitations`（本版必填，非 `ok` 状态写明局限范围与影响）；
7. `pending_human`（缺数据/单位不明/规则冲突等需人工确认项）；
8. `change_log`（每项 `time/step/action`）。

再加 `errors`、`degradations`、`runtime`、`artifacts`、`primary_artifact`。主产物要
“可打开、可编辑、可复现”：清洗表给 CSV + 逐字段规则说明，报告给 Markdown，图形给
可编辑源 + 可渲染预览。**partial 结果必须标明其局限。**

非破坏式产物集（由 `--phase full` 生成）：`source_snapshot.csv`（原文逐字快照）、
`cleaned_data.csv`（清洗值，初始与原文相同）、`change_map.csv`（单元格级变更审计）、
`data_profile.json`（字段画像）、`quality_report.md`（数字实时重算）、
`evidence_map.json`、`rules_applied.json`。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md；`--phase bootstrap` **先落盘 partial 结果骨架**，之后只做增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 逐份打开数据字典/单位/QC/工单，确认字段语义、量纲、上下界、主键；`--phase full` 生成非破坏式产物集 | 材料 → 快照/清洗表/画像/报告/证据模板 |
| 2 | 6–12 分钟 | ≤30 | 逐字段回填：缺失四分类、异常判断（对照 QC 上下界与物理范围）、重复主键、单位换算映射；把决定写入 change_map/rules_applied | 中间产物 → 修正后的结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/limitations/pending_human；写 change_log | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | `--phase selfcheck`，确认合法后结束 | result.json → 自检结论 |

每步先写盘再分析，任何中途异常都不留不完整产物。同一输入重复运行数据结果必须一致
（脚本无随机、无网络；时间戳仅作元数据）。脚本只做确定性机械工作；字典解读、规则
冲突裁决、主键判定由 Agent 完成并写进 change_log/evidence_map，不得用脚本输出冒充
人工判断，不得预写正式答案。

## 5. 自检清单（对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——正确性。
- [ ] 异常判断有物理上下界与 locator 依据，保留/剔除/盖帽理由成文——正确性。
- [ ] 重复识别基于真实主键，保留策略明确，近似重复单独报告——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失，摩尔质量换算标 pending_human——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（`--phase selfcheck` 重算核对）——可复核性。
- [ ] 每个关键结论有 locator + 逐字 quote；冲突信息并列并标 source_conflict——证据溯源。
- [ ] 非 ok 状态有 limitations；缺数据写 pending_human，不编造——降级与不确定性（本版重点）。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，不崩溃——可靠体验。
- [ ] 两次运行数据结果一致；partial 结果标注局限——可靠体验。
- [ ] `python scripts/selftest.py` 通过；`--phase selfcheck` 报告 passed——交付。

## 6. 降级策略

- 缺材料：`status=insufficient_evidence`，在 `pending_human` 列缺口，不编造数据或结论。
- 缺软件（渲染/OCR 失败）：`status` 最多 `partial`，在 `degradations`/`errors` 写失败
  步骤与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不静默处理。
- 写盘失败：降级写最小合法 result.json（骨架），不让进程崩溃（退出码 0）。
- 任何“自述增益/分数/金标准”无运行证据支撑的，视为未证实，不写入正式结论。

## 7. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python scripts/selftest.py   # 独立自检（构造临时 demo 跑三阶段 + 降级路径）
```

辅助规则见 `references/checklist.md` 与 `references/evidence_protocol.md`。
