# 字段口径、同族归并与法律状态核验规则

本文是 SKILL.md §4/§5 的可复核依据，定义号码规范化、同族归并、法律状态核验、授权边界的字段口径。所有判定以官方登记为准，观测不到的信息写 `not_reported` 或 `unknown`，不推断。

## 1. 号码规范化口径

公开号（publication）、申请号（application）、优先权号（priority）、授权号（grant）是四个不同口径，禁止混用。规范化输出统一为：

```json
{"raw": "...", "normalized": "...", "country": "US", "kind_code": "B2",
 "number_type": "grant", "confidence": "high", "format_ok": true, "notes": []}
```

- `normalized` = 国家代码 + 数字核心 + kind 码；CN 申请号校验位以 `.X` 保留（如 `CN109876543.2`）。
- `number_type` 判定规则（按可靠度降序）：
  - US：`YYYY/NNNNNN` → 公开号；`6X/NNN,NNN` → 优先权号（临时申请）；`YY/NNN,NNN` → 申请序列号；纯数字或 `,` 分组且 kind 为 B/C → 授权号。
  - WO：`YYYY/NNNNNN` → 公开号（PCT 申请公开）。
  - EP：`N NNN NNN` 或 7–8 位 → 公开号。
  - CN：带 `.X` 校验位 → 申请号；kind A → 发明公开；B/C → 授权；U/Y → 实用新型。
  - JP/KR：`YYYY-NNNNNN` 或 `YYYY-NNNNNN` → 公开号。
- `confidence`：有明确 kind 码且成功分类 → `high`；无 kind 码但格式可识别 → `medium`；无法分类 → `low`。`format_ok=false` 或 `confidence=low` 的结果必须写 `uncertainty` 并进入人工复核，不得当作官方口径使用。
- 号码抽取（`extract`）只做候选发现，逐条 `normalize` 复核后才可写入 `patent_records`。

## 2. 同族归并口径

- **简单族（simple）**：共享同一优先权（含优先权申请号）的一组成员，属官方登记的"patent family"。
- **扩展族（extended）**：经由中间优先权、分案、继续申请等传递连接的更大集合；只有官方族信息明确时才可用，否则标 `not_reported`。
- **观测族（observed）**：仅基于当前拿到的记录，两个记录在任一归并字段（优先权号、申请号、公开号）上规范化值相同即视为同一族。**本 Skill 自动归并恒标 `family_type=observed` 且 `exhaustive:false`**——观测到的链接不构成穷尽同族，不暗示法律范围相同。

归并算法：对 `priority_number / application_number / publication_number / grant_number` 四个字段取规范化键，用 union-find 合并共键记录；`basis` 记录触发合并的共享字段与键值。仅共享优先权号不足以断定权利要求范围相同，需逐条核对权利要求。

## 3. 法律状态核验口径

- `legal_status[]` 每项含：`status`（granted/lapsed/withdrawn/rejected/pending/unknown）、`effective_date`、`source`（库名+版本）、`as_of`（查询日期）、`staleness_risk`（布尔或 `high/medium/low`）、`confidence`。
- **来源优先级**：官方登记库（USPTO Patent Center / Espacenet / PATENTSCOPE / 国家局）> 官方公报 > 权威二手库（需标明二手）> 转载页（不可作为登记依据）。
- 官方状态未知就写 `unknown`，**不推断"有效"**；转载页只能作参考线索并标 `source_conflict` 或 `staleness_risk`。
- 状态冲突：双方证据并列记录，标 `source_conflict`，写 `pending_human` 建议回官方库复核。

## 4. 授权边界与绿/黄/红分级

| 分级 | 含义 | 处置 |
|---|---|---|
| 绿 | 公开且许可明确（官方公开著录项、开放许可文献） | 可展示著录项与摘要，留 `source`+`license_basis` |
| 黄 | 公开但许可受限（需注明出处、限内部） | 只展示字段名/结论，不展示受限原文 |
| 红 | 付费墙/登录受限/保密 | 不抓取、不登录、不上传、不展示；标 `degradations` |

每项数据源写 `authorization[]`：`{source, license_basis, tier, displayable_fields, retention}`。授权不足时写能力降级方案（例如"仅保留公开号与状态结论，不引用权利要求原文"）。

## 5. 证据与来源记录口径

- `evidence[]`：每条结论必须有 `locator`（如 `materials/patent.md#p3`、`/input/case.json` 字段路径、检索结果 URL）与逐字 `quote`；冲突标 `source_conflict`。
- `sources[]`：`{engine, query, url, retrieved_at, count, note}`，记录实际执行、实际返回条数与查询日期，供他人复跑。
- `change_log[]`：记录 `scaffold/checkpoint/finalize` 等动作与时间戳，保证过程可审计。

## 6. 确定性约定

同一输入重复运行，`normalize`/`extract`/`family` 结果一致：不使用随机数，输入目录遍历按排序，同族 `family_id` 按成员最小序号稳定编号。
