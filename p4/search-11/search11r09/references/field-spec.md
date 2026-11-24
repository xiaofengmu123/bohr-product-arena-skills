# 字段口径与规则（field-spec）

本文件是 `SKILL.md` 的补充规范：号码口径、同族规则、法律状态核验、授权分级与证据溯源，供 Agent 在执行时对照。

## 1. 号码规范化

- **三号分离**：公开号（publication number，含 kind 码如 A1/B2）、申请号（application/serial number）、优先权号（priority number）不可混用；字段里分别存 `publication_number / application_number / priority_number`。
- **规范化键**：国家代码（大写 2 位）+ 数字主体（保留 `/`、`.`、`-`）+ kind 码（A/A1/B1/B2/C/U/S/Y 等）。
- US：`US 12,345,678 B2` → `US12345678B2`（授权，`number_type=grant`）；`US 2010/123456 A1` 为公开号；`12/345,678` 为申请序列号。
- WO：`WO 2020/123456 A1` → `WO2020/123456A1`；EP：`EP 1 234 567 A1` → `EP1234567A1`。
- JP 优先权常见 `2020-123456`；CN 常见 `CN1234567A`。
- 无法判定类型时写 `number_type: unknown` 并加 uncertainty，不硬猜。

## 2. 同族归并

- 简单族（simple）：共用同一优先权号。
- 扩展族（extended）：通过引证/同一申请号传递关联。
- 观测族（observed）：仅就手头语料可见的关联，任何自动归并结果都标 `exhaustive:false`。
- 只依据共同申请号/优先权号归并，不因关键词命中相同而合并；证据写 `basis_field` + `basis_normalized`。
- 语料缺失成员时写 `family_scope_unclear`，不宣称“完整族”。

## 3. 法律状态核验

- 记录 `status`、`effective_date`、`source`（官方登记库名+版本）、`as_of`（查询日期）、`staleness_risk`、`confidence`。
- 转载页/聚合站不作为登记依据；官方状态未知写 `unknown`，不推断“有效”。
- 冲突双方并列记录并标 `source_conflict`；状态过期写 `staleness_risk` 与 `pending_human`。

## 4. 授权边界分级

- 绿（green）：可公开访问并展示（官方登记页、公开专利全文）。
- 黄（yellow）：需登录或受使用条款限制，可引用著录项但限制原文展示。
- 红（red）：付费墙/企业保密，不尝试抓取、不展示原文，只写来源与不可用说明。
- 每个来源记 `{source, license_basis, tier, displayable_fields, retention}`。

## 5. 证据溯源

- 每条关键结论给 `locator`（如 `materials/xxx.pdf#p3` 或 `sources[i]`）+ 逐字 `quote`。
- 不虚构引用、DOI、数值；缺失写 `not_reported` 或 `pending_human`。

## 6. 证据强度与风险

- 字段缺失 → `field_missing`；状态过期 → `staleness_risk`；同族口径不明 → `family_scope_unclear`；OCR 边界不明 → `ocr_uncertain`。
- 每条结论标注 `confidence`（high/medium/low）并给出理由。
