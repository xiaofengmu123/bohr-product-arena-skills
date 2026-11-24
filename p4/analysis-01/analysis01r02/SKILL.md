---
name: sci-data-cleaning
description: 对已发表科研聚合表转录件做可复核、有时间预算的清洗：先读数据字典、每指标单位/QC 规则与清洗工单，区分未检测/低于检出限/录入缺失/真值0，处理异常值、重复、编码与单位转换，生成与清洗表逐字段一致的质量报告并产出 result.json。内置 900 秒/64 次请求的分阶段预算与前 3 分钟落盘骨架的检查点机制，全程可回退、可复跑。用于科研数据清洗、数据质检与可复现流水线场景，当输入是带编码/存储故障的已发表聚合表转录件时使用。
version: 2.0.0
---

# 科研数据清洗 Skill（sci-data-cleaning v2）

## 1. 角色与何时使用

本 Skill 把“科研聚合表清洗”沉淀为可调用、可复核、**带时间预算与检查点**的流程。
适用：输入为已发表聚合表转录件（CSV/TSV 等），附数据字典、每指标单位/QC 规则和
清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、图表
出版级排版、数据库运维、实验方案设计。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/
删行/换单位，不得编造数据、分数或引用。** 每个关键结论必须能指回材料的逐字片段
（locator + quote）。这是对“已发表聚合表转录件”的清洗，不是对原始实验的统计推断。

## 2. 输入契约

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。
  **output_contract 字段是动态的，必须原样回填全部键**，缺值给 `null`/`[]`，不改名、
  不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用
  `scripts/main.py` 自动发现，**不得按文件名假设内容，必须逐份打开核对**。
- PDF/图片等非文本材料：用平台提供的解析工具读取原文，不要按标签臆测内容。
- 产物写 `/app/output/`，中间文件写 `/app/work/`。同一输入重复运行，数据结果必须
  一致（脚本无随机、无网络；时间戳仅作元数据）。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（列表，逐条写结论的不确定性与依据）；
6. `change_log`（列表，每项 `at/step/action`，记录每一步改了什么、为什么、依据）；
7. 另加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`（主产物路径）。

主产物“可打开、可编辑、可复现”：清洗表给 CSV（原值保留 + `_class::<列>` 标注），
报告给 Markdown，画像给 JSON，证据映射给 JSON。**partial 结果必须标明其局限。**

## 4. 执行流程与时间预算（总 900 秒、64 次模型请求）

核心设计：**写前落盘（write-ahead checkpoint）+ 只做增量改进**。任何时候中断，
`/app/output/result.json` 都是合法可加载的；每个阶段先写 `/app/work/checkpoint.json`
与 result.json，再做分析；已回填的字段只增不空，回退时在 change_log 记录。

| 阶段 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|-|-|-|-|-|
| 0 bootstrap | **前 3 分钟内** | ≤4 | 读 case.json/task.md，运行 `python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work`，**立即落盘 result.json 骨架（status=partial、回填全部 contract 键）** | /input → result.json 骨架 |
| 1 full | 3–6 分钟 | ≤15 | 读数据字典→字段语义/量纲；读单位/QC 规则；读工单。运行 `--phase full` 生成清洗表 + 数据画像 + 质量报告 + 证据映射 + 机械 change_log | 材料 → cleaned_data.csv 等 |
| 2 复核 | 6–12 分钟 | ≤30 | 逐字段核对：缺失分类、异常值（对照 QC 上下界与物理范围）、重复主键、单位转换、报告数字与清洗表一致性 | 中间产物 → 修正后的裁决 |
| 3 finalize | 12–14 分钟 | ≤10 | 将裁决写入 `/app/work/agent_decisions.json`（sources/uncertainty/pending_human/change_log/status/contract_values），运行 `--phase finalize` 合并定稿 | 裁决 → result.json 定稿 |
| 4 selfcheck | 最后 1 分钟 | ≤5 | 运行 `--phase selfcheck`，确认全部 `[ok]` 后结束 | result.json → 自检结论 |

- **bootstrap 硬约束**：开始后 180 秒内必须完成并落盘骨架；骨架之后**只做增量改进**，
  不允许清空已回填的 contract 键或从 `ok` 回退到空字段而不记录。
- 每步先写盘再分析；任何中途异常都不留不完整产物。
- Agent 在每步前检查剩余时间，预算将尽时**提前收口**为 `partial` + 诚实
  `pending_human`/`degradations`，绝不崩溃或留下空文件。

## 5. 清洗规则（保守默认，逐字段写依据）

详细展开见 `references/rules.md`，要点：

- **缺失值**：区分 未检测/低于检出限/录入缺失/真值 0 四类，保留原文并分类，互不覆盖；
  默认不插补。`below_lod`/`not_detected` 是左删失/未检出，**不是缺失、不是 0**。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准，保留/剔除/盖帽均写理由与
  locator；不用“均值±n 倍标准差”武断删除真实高值。
- **重复记录**：按真实主键识别（脚本的全列归一化只是候选建议）；近似重复单独报告，
  明确保留哪一条及依据。
- **格式/单位**：编码先于解析（UTF-8/GB18030/BOM 容错）；科学计数法、千分位、有效数字
  不静默截断；单位换算保留映射表与因子；摩尔质量类换算不猜，标 `pending_human`。
- **质量报告**：字段级清单，数字必须由清洗后表格实时重算，不手写。

## 6. 自检清单

完整勾选项见 `references/checklist.md`，机械自检两条命令：

```bash
python scripts/selftest.py                          # 包级自检：frontmatter/编译/空输入/确定性
python scripts/main.py --phase selfcheck --output /app/output
```

对应评分维度的要点：结果完整性（contract 全键回填）、实际效果（四类缺失分开、异常有
物理上下界、去重主键真实、单位无损）、证据溯源（locator+quote、冲突并列标
source_conflict）、可靠体验（空表/坏 JSON/乱码/主键冲突/权限不足均识别、退出码 0、
确定性、partial 标注局限）。

## 7. 降级策略（缺数据/缺软件时）

- 缺材料：`status=insufficient_evidence`，在 `pending_human` 列缺口，不编造数据或结论。
- 缺软件（渲染/OCR 失败）：`status` 最多 `partial`，在 `degradations`/`errors` 写失败步骤
  与原因，保留已完成的合法产物。
- 单位不明、规则冲突、主键冲突、查询失败：标 `uncertainty` 或 `pending_human`，不静默处理。
- 写盘失败：降级写最小合法 result.json 骨架，进程退出码保持 0。

## 8. 脚本调用（相对路径，仅标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase finalize  --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --output /app/output
```

脚本为确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定、异常边界）仍由
Agent 完成，写入 `agent_decisions.json`、`change_log` 与 `evidence_map.json`。不得用
脚本输出冒充人工判断，不得预写正式答案。
