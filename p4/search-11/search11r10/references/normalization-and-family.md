# 号码规范化、同族归并与授权口径

## 1. 号码规范化（`normalize`）

规范化分四步，全部可复核：

1. **去前缀**：剥离 `PUB. NO.`、`APPLICATION NO.`、`PRIORITY`、`PATENT NO.`、
   `USPTO`、`ESPACENET` 等标签；扫描件 OCR 可能把标签与号码粘连，先按文本实际内容处理。
2. **提国别/机构码**：两字母码（US/EP/CN/JP/KR/DE/GB/FR…）或 `WO`/`PCT/`（归入 WO）。
3. **分离 kind code**：末尾 `A1/A2/B1/B2/C1/U/S/Y/T…` 从号码主体剥出；
   允许紧贴数字（`US12345678B2`）或分隔符（`3 456 789 A1`）两种写法。
4. **判定号码类型**：
   - `application`：`US 17/123,456`（含 `/` 分栏）、`PCT/US2020/012345`、
     `CN 202010123456.7`、`EP 20345678.9`（年前缀/校验位格式）；
   - `publication`：带 kind code 的公开号；
   - `priority_or_other`：无法判定时如实标，`confidence` 降到 0.6。

规范化结果字段：`raw`（原文）、`country`、`number`（纯数字）、`kind`、
`normalized`（`国别:数字[:kind]`）、`number_type`、`confidence`。
**不硬猜**：国别未知标 `country_unknown`，置信度降到 0.4；无数字标 `no_digits`。

## 2. 同族归并（`family`）

- 采用**并查集 + 传递闭包**，不是按单一键分组：A 与 B 共享申请号、B 与 C
  共享优先权号时，A/B/C 归入同一族。
- 归并键仅取 `application_number` 与 `priority_number` 的规范化值；
  仅当两者都缺时，才以 `publication_number` 兜底连接，且单个公开号形成单元素族。
- 每组输出 `basis`：说明是靠哪些字段连起来的（`application_number` /
  `priority_number` / `singleton`），保证可复核。
- 所有结果统一标 `family_type: "observed"`、`exhaustive: false`：观测到的
  同族不是官方登记意义上的完整简单族/扩展族，避免把"检索命中"冒充"全族"。

## 3. 口径与降级

- **简单族 / 扩展族 / 观测族**三口径分开写；本 Skill 离线归并只产出观测族，
  需要官方库（Espacenet/INPADOC/PATENTSCOPE）才能补简单族/扩展族，此时写
  `pending_human`。
- 号码/族/法律状态**冲突并列**，标 `source_conflict`；官方状态未知写 `unknown`。
- 授权分级：绿（可公开展示官方登记字段）、黄（可展示摘要/著录项，原文受限）、
  红（受限库原文，不抓取不展示），逐源记录 `license_basis` 与 `displayable_fields`。

## 4. 已知边界

- 本实现的号码类型判定是**格式启发式**，跨多国格式时可能把个别优先权号判为
  申请号；凡不确定的，保留 `priority_or_other` 与低 `confidence`，交由人工复核。
- 单字母 kind code（A/B/C）为防误判未全部纳入，需要时人工补 `kind`。
- 不联网、不绕过付费墙、不上传受限库原文。
