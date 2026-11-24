---
name: sci-data-cleaning-robust
description: 对科研汇总表转录件做可复核、抗干扰的数据清洗：先读数据字典、单位/QC 规则与清洗工单，再区分未检测/低于检出限/录入缺失/真值 0，处理异常值、重复记录、编码与单位转换，生成与结果表数字一致的字段级质量报告并回填 result.json。重点保证空输入、坏 JSON、缺参数、超大文件、坏编码、写盘失败等异常场景优雅降级、退出码恒为 0、重复运行结果确定。适用于科研数据清洗、数据质检与可复现流水线。
version: 2.0.0
---

# 科研数据清洗 Skill（robust 版）

## 1. 角色与何时使用

本 Skill 把“科研聚合表清洗”沉淀为可调用、可复核、抗干扰的流程。适用：输入为已发表聚合表转录件（CSV/TSV/TXT 等），附数据字典、每指标单位/QC 规则与清洗工单，可能带编码/存储故障。不适用：原始实验数据的统计建模与假设检验、图表出版级排版、数据库运维、实验方案设计。

硬性红线（评分关键）：**不得插补缺失/无效源值，不得推断总体统计，不得静默改值/删行/换单位，不得编造数据、分数或引用。** 每个关键结论都要能指回材料逐字片段（locator + quote）。

本版相对基础做法的取舍：**鲁棒性优先、完整性不退让**。把“任何异常都不崩溃、都留下诚实可复核的产物”作为一等目标，具体见第 7 节。

## 2. 输入契约

运行时输入在 `/input/`，产物写 `/app/output/`，中间文件在 `/app/work/`。

- `/input/case.json`：`case_id`、`output_contract`（待回填字段）、材料清单与证据范围。**output_contract 字段是动态的，必须原样回填全部键**：缺值给 `null`/`[]`，不改名、不删未知键。
- `/input/task.md`：任务要求与约束。
- 材料：聚合表、数据字典、单位/QC 规则、清洗工单。文件名不固定，用脚本自动发现；**不得按文件名假设内容，必须逐份打开核对**。PDF/图片用平台解析工具读原文。

## 3. 输出契约（必须）

`/app/output/result.json` 必须包含：

1. `case_id`（取自 case.json）；
2. `output_contract` 的**全部字段**（原样回填，含未知键，缺值 `null`/`[]`）；
3. `status` ∈ `ok | partial | insufficient_evidence | input_error`；
4. `sources`（列表，每项 `locator` + 逐字 `quote` + `note`）；
5. `uncertainty`（列表，逐条写结论的不确定性与依据）；
6. `change_log`（列表，每项 `time/step/action`，记录每一步改了什么、为什么）。

另加 `errors`、`degradations`、`pending_human`、`runtime`、`artifacts`。主产物“可打开、可编辑、可复现”：清洗表给 CSV，报告给 Markdown，画像/证据给 JSON。**partial 结果必须标明局限**。

## 4. 执行流程（总预算 900 秒、64 次模型请求）

| 步 | 时间 | 请求上限 | 动作 | 输入 → 输出 |
|---|---|---|---|---|
| 0 | ≤3 分钟 | ≤4 | 读 case.json/task.md，运行 `python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work`，**先落盘 result.json 骨架（status=partial）**，之后只做增量改进 | /input → result.json 骨架 |
| 1 | 3–6 分钟 | ≤15 | 读数据字典→字段语义/量纲；读单位/QC 规则；读工单。运行 `--phase full` 生成清洗表 + 数据画像 + 质量报告 + 证据映射 | 材料 → cleaned_data.csv + data_profile.json + quality_report.md + evidence_map.json |
| 2 | 6–12 分钟 | ≤30 | 逐字段核对：缺失分类、异常判断（对照 QC 上下界与物理范围）、重复主键、单位转换映射、报告数字与清洗表一致性 | 中间产物 → 修正后的结果 |
| 3 | 12–14 分钟 | ≤10 | 回填 output_contract 全部字段；补 sources/uncertainty/pending_human；写 change_log；status 定稿 | 分析 → result.json 定稿 |
| 4 | 最后 1 分钟 | ≤5 | 运行 `python scripts/main.py --phase selfcheck --output /app/output` 与 `python scripts/selftest.py`，确认合法后结束 | result.json → 自检结论 |

**每一步先写盘再分析**：任何中途异常都不留不完整产物。同一输入重复运行，数据结果必须一致（脚本无随机、无网络；时间戳仅作元数据）。前三分钟骨架一旦落盘，后续只在骨架之上增补/修正，不再整体重写，保证随时可交付。

## 5. 清洗规则（保守默认，逐字段写依据）

- **缺失值**：区分 `未检测/not detected`、`低于检出限/below LOD`（左删失，非缺失、非 0）、`录入缺失`（空、NA、-999 等哨兵）、`真值 0`。四类保留原文并分类，**不互相覆盖**。默认不插补。
- **异常值**：以字典 + 单位/QC 规则给出的物理上下界为准，保留/剔除/截帽均写理由与 locator；不用“均值±n 倍标准差”武断删除真实高值。
- **重复记录**：按真实主键（样本编号、时间戳、受试者标识）识别；大小写、空格、单位写法不一致的近似重复单独报告；明确保留哪一条及依据。
- **格式/单位**：编码先于解析（自动容错 UTF-8/GBK/BOM）；科学计数法、千分位、有效数字不静默截断；单位换算保留映射表与因子；摩尔浓度等需摩尔质量的换算**不猜**，标 `pending_human`。
- **质量报告**：字段级清单（缺失率、异常条数、重复组、转换记录、处理动作与影响范围）；**报告数字由清洗后表格实时重算**，不得手写。

## 6. 自检清单（逐条勾选，对应评分维度）

- [ ] case.json 与 task.md 已读，output_contract 全部键已回填（`null`/`[]` 不丢键）——结果完整性/可复核性。
- [ ] 数据字典、单位/QC 规则、工单均已打开并按内容执行，未按文件名臆测——实际效果。
- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值——实际效果/正确性。
- [ ] 异常值判断有物理上下界与 locator 依据，保留/剔除理由成文——正确性。
- [ ] 重复识别基于真实主键，保留策略明确——正确性。
- [ ] 单位/编码转换可逆或留映射表，精度未丢失——正确性。
- [ ] 报告数字与 cleaned_data.csv 逐字段一致（可重算核对）——可复核性。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突信息两项并列并标 `source_conflict`——证据溯源。
- [ ] 空表/坏 JSON/乱码/单位不明/主键冲突/文件损坏/权限不足均识别并诚实记录，退出码 0——可靠体验。
- [ ] 两次运行数据结果一致（确定性）；partial 结果标注局限——可靠体验。
- [ ] result.json 经 `--phase selfcheck` 与 `scripts/selftest.py` 通过——交付。

## 7. 降级与健壮性策略（本版重点）

**总原则：异常不崩溃、不撒谎、不丢已完成的合法产物。退出码恒为 0，把“哪里坏了、缺什么、还剩什么”写进 result.json。**

| 场景 | 处置 |
|---|---|
| 空输入目录 / 缺材料 | `status=insufficient_evidence`，在 `pending_human` 列缺口，不编造 |
| 坏 JSON（case.json） | 捕获解析错误写入 `errors`，仍用文件名兜底 case_id，继续处理其余材料 |
| 坏编码 / BOM / 二进制 | 编码级联 `utf-8-sig → gb18030 → latin-1`（无损兜底），NUL 字节判定二进制并记录 `degradations` |
| 缺参数 / 未知参数 | `--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`，未知参数忽略并记录 |
| 超大文件 | 单文件默认 2MB 上限（可 `--max-bytes` 覆盖），超限只读前 N 行做画像并标 `truncated`，不整表进内存 |
| 写盘失败 / 权限不足 | 降级写最小合法 result.json（骨架），进程不崩溃；保留已生成的中间产物 |
| 单位不明 / 规则冲突 / 主键冲突 | 标 `uncertainty` 或 `pending_human`，冲突两来源并列标 `source_conflict`，不做静默处理 |
| 渲染/OCR/Office 失败 | `status` 最多 `partial`，在 `degradations`/`errors` 写失败步骤与原因 |

脚本是确定性辅助工具；智能判断（字典解读、规则冲突裁决、主键判定）仍由 Agent 完成并写入 `change_log` 与 `evidence_map`。不得用脚本输出冒充人工判断，不得预写正式答案。

## 8. 脚本调用（相对路径，标准库，可执行）

```bash
cd /skills/weekly-skill
python scripts/main.py --phase bootstrap --input /input --output /app/output --work /app/work
python scripts/main.py --phase full      --input /input --output /app/output --work /app/work
python scripts/main.py --phase selfcheck --input /input --output /app/output --work /app/work
python scripts/selftest.py
```

所有脚本仅用 Python 标准库，无随机、无网络；路径用相对路径，按上述顺序调用即可完成一次完整清洗与自检。

## 9. 边界

- 只解决“科研聚合表转录件的清洗与质检”一类任务，不做通用大而全的框架；抽象过度会同时降低成功率与可复核性。
- 没有真实材料时输出“材料不足”，不用示例填充正式案例。
- 不推断总体统计、不插补缺失/无效源值；无法判断处如实标 `pending_human` 或 `not_reported`。
