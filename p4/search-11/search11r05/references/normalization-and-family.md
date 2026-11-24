# 字段口径、规范化与同族归并规则

本文是 `scripts/core_impl.py` 的字段语义说明，供 Agent 在执行时对齐口径，也供复核者独立复跑。

## 1. 三类号码的字段口径（不许混用）

| 字段 | 含义 | 典型形 | 判定信号 |
|---|---|---|---|
| `publication_number` 公开号 | 公布/公开时的文献号，常带种类代码 | `US 2020/0123456 A1`、`EP 3 456 789 A1` | 尾随 A 类代码，或带年份前缀 |
| `application_number` 申请号 | 受理号，含斜杠或校验位 | `US 16/123,456`、`EP 19123456.7`、`CN 201910123456.7` | 含 `/` 或 `.`，或原文标签 `Application` |
| `grant_number` 授权号 | 授权后的专利号，常无种类代码 | `US 9,876,543`、`CN 1234567 B` | B/U/Y/S/C 类代码，或 US 纯数字 ≤8 位 |

`number_type` 取值：`application` / `publication` / `grant` / `unknown`。判定按证据强度分级：**显式标签 > 种类代码 > 分隔符（/ 或 .）> 国别+位数启发式**；每级递减时在 `issues` 中注明并降低 `confidence`（high → medium → low）。

## 2. 规范化规则（`normalize`）

1. 剥离前缀标签（Publication/Patent/Grant/Application/Serial/Priority + No./Number/#）。
2. 去除空白与逗号，转大写。
3. 取前 2 位大写字母为 `country`（在 ST.3 集合内则置信度高，否则记 `country_code_unrecognized`）。
4. 取尾部单个大写字母+可选数字为 `kind_code`（仅当字母属于 `ABCUYSMPHTDEG` 且主体长度 >2，防止误吞数字前字母）。
5. 规范化形 = `country + 数字主体（去分隔符）+ kind_code`，如 `US12345678B2`、`WO2020123456A1`。
6. 正则校验 `[A-Z]{2}[0-9A-Z]{4,16}`；不合形记 `normalized_shape_irregular`。

**边界与降级**：OCR 粘连、WO 边界合并、扫描件噪声不改写原文——只在 `issues` 记录 `ocr_uncertain`，`normalized` 保留候选而非强行猜测。号码无法可靠定位时写 `not_reported` 并进 `uncertainty`。

## 3. 同族归并口径（`family`，observed 非穷尽）

- `simple`：材料内 ≥2 件共享同一优先权号（INPADOC 简单族口径），`basis=shared_priority_number`。
- `observed`：共享同一申请号但材料内未见共享优先权，`basis=shared_application_number`。
- `observed_singleton`：材料内仅一件，或该优先权/申请号无其他成员，或记录无优先权/申请号。
- 所有输出强制带 `observed:true`、`exhaustive:false`；扩展族（经优先权链/PCT 关联）仅当材料给出对应字段时记录，否则标 `family_scope_unclear` 进 `uncertainty`。

## 4. 法律状态核验口径

- 状态来源只认官方登记页（USPTO Patent Center / Espacenet / PATENTSCOPE / 各国局），转载页不得作为登记依据。
- 每条含 `as_of`（查询日期）与 `staleness_risk`；官方状态不可得写 `status:"unknown"`，不推断"有效/失效"。
- 状态冲突：两个来源并列保留，标 `source_conflict`，写 `pending_human`。

## 5. 授权边界分级（绿/黄/红）

| tier | 含义 | 处理 |
|---|---|---|
| 绿 | 公开、可引用、可展示 | 正常引用，字段全量入 result |
| 黄 | 可访问但展示受限/需署名/限字段 | 只保留许可字段，注明 `displayable_fields` |
| 红 | 受限库/付费墙/保密 | 不抓取、不展示原文，标 `degradations` 与降级方案 |

## 6. 证据溯源与 change_log 纪律

- 每个关键结论必须有 `evidence[]` 条目：`locator`（材料/来源定位）+ `quote`（逐字引用）；无法逐字引用时不作结论。
- 所有结构变更必须经 `record_change(result, action, detail)`，自动生成 `seq/at/action/detail`；禁止直接改结构字段而不记日志。
- `change_log` 是复核轨迹：他人应能按日志顺序重放一次检索全过程。
