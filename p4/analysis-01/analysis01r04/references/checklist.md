# 字段级规则与降级检查清单

本清单是 `SKILL.md` 自检清单的展开版，Agent 在 `--phase full` 与 `--phase selfcheck`
之间逐条执行。规则默认保守：**不插补、不静默改值、不删行、不猜单位换算**。凡无法
从材料中逐字证实的处置，一律写入 `pending_human` 或 `uncertainty`，不产生“看起来对”
的假答案。

## 1. 缺失值四分类（不得互相覆盖）

| 类别 | 判定 | 处置 |
|---|---|---|
| `entry_missing` 录入缺失 | 空、`NA`、`-999` 等哨兵 | 保留原文并标注；不插补 |
| `not_detected` 未检测 | `未检出` / `ND` / `not detected` | 与缺失、0 分开；不填 0 |
| `below_lod` 低于检出限 | `<LOD` / `低于检出限` 等左删失 | 左删失，非缺失、非 0 |
| `true_zero` 真值 0 | 字面 `0` / `0.0` | 保持 0，不并入缺失 |

脚本 `core_impl.classify_missing` 完成机械分类；Agent 负责核对字典里是否定义了
额外的哨兵/检出限写法，并在 `rules_applied.json` 记录，不得覆盖默认分类结果。

## 2. 异常值判定

- 只以**数据字典 + 单位/QC 规则给出的物理上下界**为准，逐字段写 `locator` 依据。
- 不得用“均值±n 倍标准差”武断删除真实高值；分布诊断只作为提示，不作为删除理由。
- 动作三选一并写明理由：保留 / 剔除 / 盖帽（`action` 字段，见 `change_map.csv`）。
- 规则文本中的上下界若来自脚本 `core_impl.extract_qc_bounds` 的正则提取，必须先由
  Agent 确认（正则项均带 `needs_review: true`），未确认不得使用。

## 3. 重复记录

- 以真实主键识别：样品编号、时间戳、受试者标识；主键必须由 Agent 依据字典确认后
  写入 `rules_applied.json.primary_keys`。
- 大小写、空格、单位写法不一致的近似重复**单独报告**，保留哪一条及依据必须成文。
- 脚本只做分组与报告，不自动删行；保留/合并决定由 Agent 写入 `change_map.csv`。

## 4. 格式 / 单位

- 编码先于解析：`read_text_robust` 依次尝试 UTF-8-sig → UTF-8 → GB18030 → Latin-1，
  并记录实际编码与 `degraded` 标记，不靠文件名猜。
- 科学计数法、千分位、有效数字不做静默截断；原文保留在 `source_snapshot.csv`。
- 单位换算仅使用 Agent 在 `rules_applied.json.unit_conversions` 中显式给出的映射与
  因子；摩尔浓度等需要摩尔质量的换算**不猜**，标 `pending_human`。

## 5. 质量报告

- `quality_report.md` 的字段级数字由 `core_impl.profile_table` 从清洗表实时重算，
  **不得手写**；Agent 只补充解释性文字与依据，不重写数字。
- 报告必须与 `cleaned_data.csv` 逐字段一致；`--phase selfcheck` 会重算并比对，不一致
  记为未通过。

## 6. 降级阶梯（status 的选择）

| status | 触发条件 | 必填字段 |
|---|---|---|
| `ok` | 主产物齐备、数字可复核、无未决关键缺口 | `sources` + `change_log` |
| `partial` | 已有合法产物，但存在标注的局限 | 加 `limitations` |
| `insufficient_evidence` | 关键材料缺失，无法支撑结论 | 加 `limitations` + `pending_human` |
| `input_error` | 输入损坏 / 空表 / 无法解析 | 加 `errors` + `limitations` |

任何非 `ok` 状态都必须在 `limitations` 中写明局限范围与影响，缺数据时在
`pending_human` 列出缺口，**不编造数据或结论**。同一输入两次运行的数据结果必须
一致（脚本无随机、无网络；时间戳仅作元数据）。
