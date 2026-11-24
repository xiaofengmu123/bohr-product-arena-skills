# 实验方案可行性审查 — 判定口径与检查项库

本文件是 `SKILL.md` 第 5 节的展开：给出七类约束的判定状态、判定依据、可复用的检查项库、算术规则与降级规则。所有判定都必须能由第二个人按 `result.json` 复跑得到相同结论。

## 1. 判定状态（`verdict`）

| 取值 | 含义 | 是否必须带证据 |
|---|---|---|
| `ok` | 方案已明确写清，且与材料/约束一致 | 是 |
| `conditional` | 有条件可行，条件逐条可执行 | 是（并写 `conditions`） |
| `blocked` | 缺关键资源/审批/参数，当前不可开工 | 是 |
| `unconfirmed` | 信息不足或需专家/外部确认 | 否（写 `needs_expert`） |
| `missing` | 方案本身未写该项 | 否（写缺口） |
| `conflict` | 两处材料对同一指标说法不一致 | 是（两处都列） |

资源维度额外用 `resource_state`：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。置信度 `confidence`：`high | medium | low`。

## 2. 七类约束的判定依据

| 维度 | 核心问题 | 判定依据来源 | 典型结论 |
|---|---|---|---|
| `assumptions_variables` | 假设是否可检验？自变量/因变量/混杂变量是否明确？ | 方案章节、方法文献 | 假设不可测 → `conditional`/`missing` |
| `controls` | 对照、随机化、盲法是否完整？ | SOP、方案方法段 | 缺阳性对照 → `conditional` |
| `equipment_materials` | 型号/精度/附件/耗材 vs 现有清单 | 设备清单、采购记录、手册 | 需采购 → `conditional`；不具备 → `blocked` |
| `time_cost` | 设备占用+人工+等待+周转是否算得清？ | 步骤、排期、货期 | 超出窗口 → `blocked`/`conditional` |
| `parameter_ranges` | 每个参数是否有依据与容差？越界如何判？ | 方法文献、SOP、预实验 | 无依据 → `missing`/`unconfirmed` |
| `risks_exceptions` | 故障/批次/样本不足/越界的降级路径？ | SOP、历史记录 | 有预案 → `ok`；无 → `missing` |
| `ethics_safety` | 动物/人体/危化/个人数据 → 审批级别？ | 伦理规范、审批表单 | 未批 → `unconfirmed`/`blocked` |

## 3. 检查项库（可直接复用）

下列检查项覆盖七类约束的高频判定点；每条须在 `result.json` 的对应维度落成一条带 `verdict` 的审查项，并在 `evidence` 给出定位与原文。ID 前缀为该维度缩写：`av`（假设与变量）、`ct`（对照）、`eq`（设备材料）、`tm`（时间成本）、`pr`（参数范围）、`rk`（风险异常）、`et`（伦理安全）。

### 3.1 假设与变量（`assumptions_variables`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `av-1` | 研究目标是否被转成可证伪的假设（含方向与判据）？ | 方案目标段 | `missing`/`conditional` |
| `av-2` | 自变量是否给出取值域与操控方式？ | 方案方法段 | `missing` |
| `av-3` | 因变量是否给出测量指标、单位与检测方法？ | 方案方法段、SOP | `missing` |
| `av-4` | 混杂变量是否被识别并控制？ | 方案、文献 | `conditional` |
| `av-5` | 假设检验所需的样本量/效应量是否说明？ | 统计段 | `unconfirmed` |
| `av-6` | 关键术语与操作定义是否一致（不同章节不矛盾）？ | 全文 | `conflict` |

### 3.2 对照组（`controls`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `ct-1` | 是否设空白/阴性对照？ | 方案方法段 | `conditional` |
| `ct-2` | 是否设阳性对照以证明体系有效？ | SOP、文献 | `conditional` |
| `ct-3` | 是否说明随机化分组方式？ | 方案 | `conditional` |
| `ct-4` | 是否说明盲法或客观读数以降低偏倚？ | 方案 | `conditional` |
| `ct-5` | 对照与实验组的处理是否除目标变量外一致？ | 方案 | `conditional` |
| `ct-6` | 对照数量/重复是否足够？ | 统计段 | `unconfirmed` |

### 3.3 设备材料（`equipment_materials`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `eq-1` | 所需设备型号/量程/精度是否与现有清单一致？ | 设备清单、手册 | `conditional`/`blocked` |
| `eq-2` | 关键附件（转子、夹具、探头）是否齐备？ | 设备清单 | `conditional` |
| `eq-3` | 耗材规格（批次、级别、纯度）是否满足方法要求？ | 采购记录、SOP | `conditional` |
| `eq-4` | 试剂货期是否赶得上实验窗口？ | 采购记录 | `conditional`/`blocked` |
| `eq-5` | 校准/检定是否在有效期内？ | 计量记录 | `unconfirmed` |
| `eq-6` | 设备排期是否冲突、需等待多久？ | 预约合同/排期 | `conditional` |
| `eq-7` | 是否具备替代/外协方案？ | 方案、协作清单 | `missing` |

### 3.4 时间成本（`time_cost`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `tm-1` | 每步设备占用是否估算并求和？ | 方案步骤 | `missing` |
| `tm-2` | 人工操作工时是否估算？ | 方案步骤 | `missing` |
| `tm-3` | 等待周期（孵育、培养、测序周转）是否计入？ | SOP、服务周期 | `missing` |
| `tm-4` | 总工期是否落在项目窗口内？ | 排期 | `blocked` |
| `tm-5` | 是否给出算式（`computation`）而非单一数字？ | 审查记录 | `conditional` |
| `tm-6` | 关键路径与可并行步骤是否区分？ | 方案排期 | `conditional` |

### 3.5 参数范围（`parameter_ranges`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `pr-1` | 每个关键参数是否给出目标值与允许区间？ | 方法文献、SOP | `missing` |
| `pr-2` | 区间是否引自已发表方法或预实验？ | 文献/预实验记录 | `unconfirmed` |
| `pr-3` | 容差是否与检测精度匹配？ | 设备手册 | `conditional` |
| `pr-4` | 越界如何判定与处置？ | SOP | `conditional` |
| `pr-5` | 是否存在参数间耦合约束（如温度-时间）？ | 文献 | `missing` |
| `pr-6` | 参数单位与量纲是否全篇一致？ | 全文 | `conflict` |

### 3.6 风险与异常（`risks_exceptions`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `rk-1` | 设备故障是否有备用方案/外协？ | SOP、协作清单 | `missing` |
| `rk-2` | 材料批次差异是否有验收/复测标准？ | 质控 SOP | `missing` |
| `rk-3` | 样本量不足是否有补样/降级路径？ | 样本管理 | `conditional` |
| `rk-4` | 参数越界是否触发停批与复核？ | SOP | `conditional` |
| `rk-5` | 是否设置转人工/上报节点？ | 方案 | `missing` |
| `rk-6` | 关键风险是否有量化影响估计？ | 风险表 | `unconfirmed` |

### 3.7 伦理与安全（`ethics_safety`）

| ID | 检查点 | 判定依据来源 | 不满足时 |
|---|---|---|---|
| `et-1` | 是否涉及动物/人体样本/个人数据？ | 方案材料段 | `missing`（须明确） |
| `et-2` | 对应审批级别与材料清单是否明确？ | 伦理规范、审批表单 | `unconfirmed` |
| `et-3` | 审批是否已获批、预计多久？ | 审批记录 | `unconfirmed`/`blocked` |
| `et-4` | 危险试剂是否需资质/备案/防护？ | 安全规范 | `conditional` |
| `et-5` | 知情同意/样本保藏期限是否合规？ | 伦理规范 | `unconfirmed` |
| `et-6` | 废弃物与生物安全处置是否写明？ | 生物安全 SOP | `missing` |

> 复用方式：按实际方案裁剪本库，删除不涉及的项，但**不得把"未写"当作"已满足"**；每保留一条都须落到 `review.<维度>` 并给证据。

## 4. 算术规则（用 `scripts/review.py`，不靠口算）

- **时间归一化**：`parse_duration("30分钟")→0.5h`、`"2小时"→2.0h`、`"3天"→24h`（工作日 8h/天）、`"2周"→80h`（40h/周）、`"1月"→160h`。无法解析的项写入 `notes`，不得静默丢弃。
- **时间累加**：`sum_time([{label,duration},…])` 返回 `total_hours` 与逐项 `breakdown`，把 `formula` 写进审查项的 `computation`。
- **范围判定**：`check_range(value, low, high, tol)` 返回 `in/below/above/border/out/unknown`；越界即 `blocked` 或 `conditional`，并写容差来源。
- **样本/成本求和**：`sum_numeric(rows, key)` 返回 `total` 与逐行明细；样本量不足、成本超预算须据此判定，不得给"感觉一个数"。

算术结论必须落到 `computation` 字段（如 `"0.5h+2h+3×40h=122.5h"`），使他人可复核。

## 5. 证据与冲突

- `evidence` 为数组，每项 `{locator, quote, kind}`；`locator` 用 `case.json#plan.<i>.steps[<j>]`、`case.json#contracts.<key>`、`materials/<名>.md#<节>`、`sources/<名>.pdf#p<页>`、`task.md#<行>`。
- `quote` 必须是原文逐字片段；过长截取决定性子句并给定位，不转写、不补全。
- 同一指标两源不一致：两条都记，`verdict="conflict"`，`notes` 标 `source_conflict`，不擅自选一个当真值。

## 6. 降级与诚实标注

| 情形 | 处置 |
|---|---|
| 输入目录为空/无可读文件 | `status="insufficient_evidence"`，在 `limitations` 与 `pending_human` 列缺口，不编造 |
| `case.json` 无法解析 | `status="input_error"`，记 `errors`，仍保留契约骨架 |
| 坏编码/超 2MB/缺页 | 记 `degradations`，继续处理其余材料 |
| PDF 解析工具不可用 | 记 `degradations`，`limitations` 注明"该证据未能读取"，不假装已读 |
| 伦理/安全/设备可用性未确认 | `verdict="unconfirmed"` + `needs_expert=true`，不写成可执行事实 |
| 写盘失败 | 降级到最小合法 JSON，进程退出码 0 |

任何"缺失"必须显式出现在 `missing`/`unconfirmed`/`limitations` 中，**不得默认满足**。

## 7. 判定示例（示意写法，非正式案例结果）

以下示例用于校准判定口吻，均为**示意**，不冒充任何正式案例的结论。跨实验类型的示例说明同一套七类检查项如何落到不同场景。

### 7.1 通用反例

- **把"没写"当"满足"的错误写法**：方案未提阳性对照 → 不应判 `ok`；应判 `conditional`（条件：补阳性对照孔）或 `missing`。
- **时间给"感觉一个数"的错误写法**：写"约需 2 周" → 应改为 `sum_time` 结果：`0.5h+2h+3×40h=122.5h≈15.3 工作日`，并在 `computation` 留算式。
- **越界判定**：孵育温度目标 37.0 ℃，方案允许 36.5–37.8 ℃，检测记录 40.1 ℃ → `check_range` 返回 `above` → 该参数项 `blocked` 或 `conditional`，写明重测/校准条件。
- **资源状态**：方案用到的离心机现有清单有同型号 → `resource_state="已具备"`；仅有低速替代 → `可外协`；无 → `需采购`/`不具备`。
- **伦理未定**：使用人外周血样本但审批表单未见批件号 → `verdict="unconfirmed"`、`needs_expert=true`，`pending_human` 写"需伦理委员会批件号与知情同意版本"。
- **冲突**：方案写"每孔 5000 细胞"，SOP 写"每孔 10000 细胞" → 两条都记，`conflict`，`notes=["source_conflict"]`。

### 7.2 细胞学实验（以活力检测为例）

- **资源**：方案要求"酶标仪（450 nm）"，现有清单仅有 630 nm 滤光片 → `eq-1` `conditional`，`conditions=["确认 450 nm 滤光片可采购或可外协读数"]`，`evidence` 指向设备清单行。
- **样本量**：每组 3 复孔、6 组 → `sum_numeric` 得 18 孔；若方案称"96 孔板一块"则余量充足 → `pr` 无需判越界；若方案称"每组 2 复孔" → 建议 `conditional`（复孔不足，统计功效存疑）。
- **时间**：铺板 2h + 孵育 4h + 读数 1h + 数据分析 3h → `sum_time` = 10h；若窗口为"当日完成" → `ok`；若跨日运输且无培养条件 → `tm` 判 `conditional`。
- **对照**：方案仅设空白孔、未设阳性药对照 → `ct-2` `conditional`，条件"补充已知阳性药对照孔以验证体系"。

### 7.3 动物实验

- **伦理**：方案使用小鼠但未见伦理批件号与麻醉方案 → `et-2`·`et-3` `unconfirmed`、`needs_expert=true`，`pending_human` 明确"需 IACUC 批件号、麻醉镇痛方案、人员资质"。
- **样本/时间**：n=10/组、3 组、给药 14 天、观察 7 天 → `sum_time` 含饲养与观察周期；"2 周"窗口不足以覆盖"给药 14 天+观察 7 天=21 天" → `tm-4` `blocked`，写明需延长至 ≥21 天。
- **对照**：缺假手术组或溶剂对照组 → `ct` `conditional`，条件"补溶剂对照与假手术对照"。
- **风险**：未写动物意外死亡/脱失的补样与上报路径 → `rk-3`·`rk-5` `missing`，`notes` 指出缺口。

### 7.4 测序/组学实验

- **设备/材料**：方案要求"PE150 测序、≥30× 深度"，现有平台仅 PE100 → `eq-1` `conditional`（需外协或降深度并说明统计影响）。
- **参数**：测序深度 30× 是否引自方法文献、容差如何 → `pr-2` 若未给依据 → `unconfirmed`，`pending_human` 请方法学专家确认。
- **时间**：建库 2 天 + 测序 5 天 + 生信 7 天 → `sum_time` = 14 天 > "10 天窗口" → `tm-4` `blocked`，`computation` 留算式。
- **风险**：未写建库失败/低质量样本的重建库路径 → `rk-1` `missing`。

### 7.5 化学合成/分析

- **安全**：使用易燃/剧毒试剂但未写通风橱、废液分类与资质 → `et-4` `conditional`；缺备案 → `blocked`。
- **参数**：反应温度"回流"未给具体区间与容差 → `pr-1` `missing`；若给"80±5 ℃"且引自已发表路线 → `ok`（附文献定位）。
- **时间**：投料 1h + 反应 6h + 后处理 3h + 表征 2 天 → `sum_time` ≈ 2.5 天；需与仪器排期对齐，冲突则 `tm` `conditional`。
- **设备**：所需核磁/质谱是否共用、排期多长 → `eq-6` `conditional`，条件"预约共用仪器并确认档期"。

### 7.6 人体样本/临床数据

- **伦理**：涉及可识别个人数据但未见数据使用协议与去标识方案 → `et-1`·`et-5` `unconfirmed`，`needs_expert=true`。
- **冲突**：知情同意书写"仅本中心使用"，方案写"多中心共享" → `et` `conflict`，`notes=["source_conflict"]`，两条证据均列。
- **风险**：样本保藏温度（−80 ℃）与运输条件未写 → `rk` `missing`，`notes` 指出缺口。

> 使用提示：以上示例只示范**判定结构与口吻**。实际审查必须读真实材料，`quote` 取真实原文；不得把示例文本抄进正式结果。
