# 清洗规则细则（sci-repro-clean）

本文件把 SKILL.md 第 5 节的默认规则展开为可核对的字段级口径。规则只做**可逆或留痕**的处理；任何不可逆操作都必须在 `change_log` 记录动作、理由与 locator。

## 1. 缺失值四分类（不互相覆盖）

| 分类 | 判据示例 | 处理 | 落盘 |
|---|---|---|---|
| `entry_missing` 录入缺失 | 空串、`.`、`-`、`NA`、`null`、`-999`、`9999`、`缺失` 等哨兵 | 保留原文，不填 0、不填均值 | `<col>` 原值；`<col>__class=entry_missing` |
| `not_detected` 未检测 | `ND`、`n.d.`、`not detected`、`未检测`、`未测定` | 与"低于检出限"区分，不当作 0 | 同上 `not_detected` |
| `below_lod` 低于检出限 | `<LOD`、`<0.01`、`LOD`、`LOQ`、`未检出`、`低于检出限` | 左删失，保留原文并标记，不换算、不归 0 | 同上 `below_lod` |
| `true_zero` 真值 0 | `0`、`0.0`、`0e0` | 明确保留为 0 | 同上 `true_zero` |

`<col>__numeric` 仅对可解析数值填 `Decimal` 字符串；四类缺失的 `__numeric` 留空。统计缺失率按四类口径分别计，不合并、不互吞。

## 2. 异常值

- 判据只取字典/QC 规则给出的物理上下界（如 `[0, 100]`、`单位: mg/L, 范围 0.1–50`）。
- 超界只打标 `outlier_low` / `outlier_high`，默认**保留**该行。
- 只有工单明确写"剔除/截帽"时才执行，且必须在 `change_log` 记录理由、locator 与影响行数。
- 不使用"均值±nσ"作为自动删除依据；若需分布诊断，仅作为 `uncertainty` 备注，不自动改值。

## 3. 重复记录

- 主键优先取字典/工单声明的 `sample_id`/`subject_id`/`timestamp`/`编号`/`标识`。
- 归一化：NFKC 规范、折叠空白、统一 `μ/µ` 与 `mg/L` 大小写；据此识别近似重复。
- 保留**首条**（原行序），其余行 `__keep=false`、`__duplicate_of` 指向保留行号。
- 找不到主键时退化为全列比对，并在 `pending_human` 提示人工确认主键。

## 4. 编码与格式

- 读取顺序：`utf-8-sig` → `utf-8` → `gb18030` → `gbk` → `big5` → `cp1252` → `shift_jis` → `latin-1`；含 NUL 时尝试 UTF-16。
- 启发式修复 UTF-8 双编码乱码（如 `Ã©`、`â€`），修复动作记入 `degradations`。
- 分隔符用 `csv.Sniffer` 探测（`,`、`\t`、`;`、`|`）；支持带引号字段与内嵌换行。
- 数值用 `decimal.Decimal` 解析与换算：千分位逗号、科学计数法（`1.2e-3`）均保精度，不静默截断有效位。

## 5. 单位换算

- 只使用规则中显式给出的因子（如 `1 mg/L = 1 μg/mL`、`mg/L:1`）；换算因子表写 `unit_map.json` 与 `run_manifest.json`。
- 换算仅在"同字段跨记录单位不一致且因子已知"时执行，并把动作写入 `change_log`。
- 摩尔浓度（`mol/L`、`mmol/L`、`μM` 等）在缺摩尔质量时**不猜换算**，标 `pending_human`。

## 6. 质量报告一致性

- `quality_report.md` 的所有数字在写 CSV 的同一份内存行对象上实时重算。
- `selftest.py --mode result` 会重新解析 `cleaned_data.csv` 复核总行数/保留行数/重复行数，报告与表不一致即判失败。
- 冲突信息（同一指标两个来源不一致）并列记录并标 `source_conflict`，不静默取其一。

## 7. 确定性契约

- 无随机数、无网络、无环境依赖的读取顺序；文件遍历排序、JSON `sort_keys`、CSV 保原行序。
- 内容指纹 `canonical_fingerprint` 递归排序键并剔除 `runtime`/`time` 等易变键；`selftest.py --mode determinism` 双跑比对。
- `run_manifest.json` 记录每个输入/输出 SHA-256，供审计与增量更新。
