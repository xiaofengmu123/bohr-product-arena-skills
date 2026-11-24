# 不确定性校准与禁编造门控

本版与参考包的核心差异：把 `limitations` / `uncertainty` 显式化，缺数据时不补造。任何结论都必须带证据分级与置信依据；任何无法核对的事实都不写成结论。

## 1. 证据分级（evidence_grade）

| 级别 | 含义 | 判定标准 | 例子 |
|---|---|---|---|
| G0 | 不可知 | 材料中无对应信息，且无法由现有数据推出 | 未提供的重复数、设备日志、机理 |
| G1 | 直接观测 | 可从原始文件逐字/逐行直接复算 | "data/batch_12.csv#L34 的 reading=9.9" |
| G2 | 可推导 | 由 G1 经明确统计方法（写明方法与阈值）推出 | "z-score=4.2 > 3.0，判为异常" |
| G3 | 假设推断 | 需专家确认或补充实验验证，必须列支持/排除证据 | "异常可能由夹具滑移导致（低置信）" |

置信标 `高/中/低` 并用 `confidence_basis` 说明依据：样本量、重复数、对照是否齐全、阈值是否先验。缺对照/重复不足只能标低置信。

## 2. 不确定性条目（uncertainty[*]）

| 字段 | 说明 |
|---|---|
| `type` | `unit_ambiguity` / `method_ambiguity` / `threshold_ambiguity` / `missing_control` / `insufficient_replicates` / `over_range_or_saturation` / `source_conflict` / `encoding` |
| `locator` | 指向具体文件/列/行 |
| `impact` | 对哪条结论造成何种影响（降级到 G0/G3、降置信） |
| `resolution_if_any` | 若有可解决手段则写，否则写 `null` |

规则：单位不一致、读数超量程或饱和 → 保留原始单位，写入 `uncertainty`，不做无依据换算或插补；对照缺失/重复数不足 → 结论降置信并写入 `limitations` 与 `pending_human`。

## 3. 禁编造门控（fabrication_gate）

`must_not_invent` 禁止补造：

1. 湿实验测量值（未在材料中出现的读数/浓度/重复数）
2. 异常的原因或机理（无排除过程时）
3. 统计显著性与 p 值（未实际计算时）
4. 文献 DOI、页码、标题（未在材料中逐字出现时）
5. benchmark 得分与白板对照结果（由平台固定）
6. 设备批次号、校准状态、运行日志（未提供时）

`non_fabrication_checks` 对每条关键数值/事实逐条核对三问：

- 该值是否在材料中**逐字出现**？（是 → G1；否 → 继续问）
- 该值是否由我**实际计算**得到？算法与阈值是否记录？（是 → G2）
- 该值是否来自**平台/外部来源**而非我推测？（是 → 注明来源；否 → G0，写 `not_reported` 或 `pending_human`）

三问都为否的，禁止写成事实。

## 4. 降级决策表

| 情形 | 动作 |
|---|---|
| 无数据/空输入 | `status=insufficient_evidence`，退出码 0，列缺口，不填充示例 |
| 坏 JSON/坏编码 | 记 `errors`/`degradations`，继续处理其余材料 |
| 二进制 XLSX/MAT 无法解析 | 记 `degradations`，提示平台导出 CSV 后重跑画像 |
| 单位不一致/超量程/饱和 | 保留原始单位，写 `uncertainty`，不插补 |
| 对照缺失/重复数不足 | 结论降置信，写 `limitations` + `pending_human` |
| 两来源冲突 | 两条都记录并标 `source_conflict` |
| 缺软件/缺依赖 | 如实标注 limitations；脚本仅用标准库兜底 |

## 5. 复核清单模板（可直接复用）

定稿前逐条勾选（与 SKILL.md 第 9 节一致）：

- [ ] result.json 可 `json.load`，`case_id` 非空，`status` 合法
- [ ] output_contract 全部字段存在，不改名不删键
- [ ] 五段 interpretation 全部非空或如实写空并标注原因
- [ ] 每条结论带 evidence（locator+quote）、evidence_grade、confidence、confidence_basis
- [ ] 异常点写明阈值方法+数值，原因写明支持/排除证据与可检验性
- [ ] 复核步骤为可执行动作（含 target/判据）
- [ ] 单位/方法/阈值歧义写入 uncertainty
- [ ] 缺失材料/坏编码/超量程/对照缺失/重复不足写入 limitations/degradations
- [ ] 未经专家确认事项进 pending_human，不写成事实
- [ ] fabrication_gate.non_fabrication_checks 已逐条核对
- [ ] change_log 覆盖骨架→画像→结论→定稿；runtime 含耗时
- [ ] 重复运行同一输入结果一致（确定性排序、无随机数依赖）
