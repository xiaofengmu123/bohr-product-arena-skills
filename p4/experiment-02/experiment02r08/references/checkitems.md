# 可复用检查项库 + 案例字段地图 + 输出字段说明（checkitems）

本文件沉淀三件可复用资产：① 七类检查项库（可直接勾选，判定依据指向 SOP/设备手册/伦理规范/合同/文献）；② 案例字段发现地图（`main.py` 的同义键名口径）；③ `result.json` 字段说明。供人工复核、复用与维护。

## 1. 检查项库

编号规则：`[维度前缀][序号]`；维度前缀 AV（假设变量）、CT（对照）、EQ（设备材料）、TC（时间成本）、PR（参数域）、RX（风险异常）、ES（伦理安全）。

### 假设与变量（AV）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| AV-1 | 研究目标是否写成可检验问题（对象/指标/判读标准） | 方案原文 | `missing` |
| AV-2 | 自变量/因变量/混杂变量是否列全并定义 | 方案原文、文献 | `missing` |
| AV-3 | 关键假设是否有边界条件（如回收率下限、批间差上限） | 文献、SOP | `missing` |

### 对照组（CT）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| CT-1 | 阳性对照能否验证方法有效性 | 方法学文献 | `missing` |
| CT-2 | 阴性/空白对照能否排除本底与交叉污染 | 方案、SOP | `missing` |
| CT-3 | 内标/质控（QC）是否覆盖全程并规定失败重做标准 | SOP | `missing` |
| CT-4 | 对照组数量与随机/盲法设计是否足以支持结论 | 方案、统计规范 | `conditional` |

### 设备材料（EQ）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| EQ-1 | 设备型号/精度/附件是否与合同或清单一致 | 设备手册、合同 | `missing` |
| EQ-2 | 设备状态判定（已具备/可外协/需采购/不具备/未知） | 库存清单、预约合同 | `unconfirmed` |
| EQ-3 | 耗材规格与被测样品匹配 | 供应商说明、文献 | `conditional` |
| EQ-4 | 货期是否赶得上排期 | 采购/预约合同 | `missing` |
| EQ-5 | 备用设备或外协替代是否写明 | 方案原文 | `missing` |

### 时间成本（TC）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| TC-1 | 单步工时与人工/机时/等待是否拆分 | 步骤、排期 | `missing` |
| TC-2 | 总工时=Σ步骤工时的算式是否可复核 | `checks.py` T1 | `missing` |
| TC-3 | 采购总额=Σ price×qty 与预算上限比较 | `checks.py` B1、合同 | `missing` |
| TC-4 | 检测周转（送样—出报告）是否计入排期 | 检测中心 SOP | `missing` |
| TC-5 | 关键路径是否落在工期窗口内 | `checks.py` W1 | `missing` |

### 参数范围（PR）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| PR-1 | 关键参数是否有名义值与容差 | 设备手册、SOP | `missing` |
| PR-2 | 参数是否落在允许域 [min,max] / allowed | `checks.py` P1 | `missing` |
| PR-3 | 参数容差的依据是否给出来源 | 文献、SOP | `missing` |
| PR-4 | 越界时是否写降级或替换参数 | 方案原文 | `conditional` |

### 风险与异常（RX）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| RX-1 | 设备故障的降级路径与备用方案 | 方案原文 | `missing` |
| RX-2 | 材料批次差异的质控与放行标准 | SOP | `missing` |
| RX-3 | 样本量不足时的停止规则与补救 | 方案、伦理方案 | `conditional` |
| RX-4 | 参数越界的报警与处置 | 设备手册 | `missing` |
| RX-5 | 转人工节点（谁、什么条件、多久响应） | 方案原文 | `conditional` |

### 伦理与安全（ES）
| id | 检查项 | 判定依据 | 缺失时 |
|---|---|---|---|
| ES-1 | 是否涉及人体/动物/危险试剂/个人数据 | 方案原文 | `missing` |
| ES-2 | 对应审批级别与所需材料是否明确 | 伦理规范、机构 SOP | `unconfirmed` |
| ES-3 | 审批当前状态（已批/在审/未申请）是否写明 | 审批表单 | `unconfirmed` |
| ES-4 | 样本来源与知情同意是否合规 | 伦理材料 | `unconfirmed` |
| ES-5 | 危险废弃物的处置路径 | 安全 SOP | `missing` |

## 2. 案例字段发现地图（main.py 的同义键名）

| 概念 | 识别的键名（不区分大小写） |
|---|---|
| case_id | case_id, caseId, id, challenge_id, armiId, slug |
| 研究目标 | research_goal, goal, objective, research_question, question |
| 候选方案 | plans, candidate_plans, plan_versions, variants, proposals, solutions |
| 资源/价格/预约合同 | contracts, resource_contracts, bookings, quotes, pricing, supply, equipment |
| 参数域 | parameters, parameter_domain, parameter_domains, domains, specs |
| 样本 | samples, sample, specimens, cohort, subjects |
| 伦理与安全 | ethics, safety, compliance, approvals, permits, irb |
| 来源文献 | sources, references, literature, citations, evidence |
| 结果契约 | output_contract, output_schema, result_contract |

合同归一化兼容 `list`、`dict-of-dict`（如 `contracts.equipment[...]`）、`dict-of-list` 三种形态；
方案时长字段识别 `duration/time/hour/day/week/min/turnaround/lead/hands/occup/wait/_h/_hrs/_d` 等后缀。
**发现不等于假设**：脚本只抽取结构，语义由审查员按原文判定。

## 3. result.json 字段说明

| 字段 | 含义 | 缺失/降级时 |
|---|---|---|
| case_id / status | 输入标识与诚实状态 | `unknown` / `insufficient_evidence` |
| research_question / plan_set | 可检验问题与候选方案清单 | `null` / `[]` |
| review.{7 维} | 七类证据化判断数组 | 空数组（S0 骨架） |
| arithmetic | `plans[]/derivations[]/not_computable[]` | `not_computable` 说明 |
| plan_comparison | 多方案确定性排序 | 单方案时为空 |
| recommendation / confirmation | 可执行结论与四类确认项 | `null` 首项 |
| evidence_index / sources | 证据索引与来源清单 | `[]` |
| uncertainty / pending_human / limitations | 缺口与待人工项 | 非 ok 必填其一 |
| errors / degradations | 读取失败与软件降级 | 如实记录 |
| rubric_coverage | 五维评分点→流程/自检落点 | 骨架内预置 |
| baseline_comparison | 声明对照分数由平台生成 | `provided_by=platform` |
| change_log / runtime / replay_digest | 修订链、运行元信息、复跑摘要 | rev 严格递增 |
| artifacts | 交付物清单（路径须存在） | `[]` |

## 4. 维护信息

- 新增检查项：在 §1 相应维度表格加一行 `[前缀][序号]`，并在用例中验证 `verdict` 枚举与证据要求。
- 修改算术规则：同步更新 `rubric.md §5` 与 `scripts/checks.py` 的对应函数，保持 `rule_ref` 一致。
- 键名地图：若组织方案例出现新同义键，加到 `scripts/main.py` 的 `*_KEYS` 常量与本文 §2。
- 自检项：`scripts/selftest.py` 的 `CHECKS` 列表明细；新增检查项请同步 `RUBRIC_COVERAGE` 的 `check` 引用。