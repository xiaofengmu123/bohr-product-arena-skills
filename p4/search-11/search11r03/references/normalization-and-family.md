# 规范化、同族归并与法律状态口径

本文定义 Skill 的确定性规则口径。脚本只做本地确定性处理，不做网络检索；Agent 在脚本输出之上补证据与来源。

## 1. 号码规范化（normalize）

输入任意字符串，输出 `{raw, country, number, kind, number_type, normalized, confidence, notes}`。

步骤（确定性、无歧义优先级）：

1. 半角化（NFKC）、统一空白与连字符、去掉分组逗号。
2. 从尾部剥离 kind code（单字母 + 0–2 位数字，字母须在 `ABCEFGHSTUY`）。
3. 从头部剥离 2 字母国家/组织代码（WIPO ST.3）。
4. 依结构判定号码类型：

| 结构 | 例子 | number_type | 说明 |
|---|---|---|---|
| 国家 + 4位年/序号 + A/B kind | `WO 2020/123456 A1` | publication | PCT/美国申请公开号 |
| 国家 + 系列/序号 | `US 16/123456` | application | 申请序号 |
| 国家 + 系列/序号（系列 60/61/62） | `US 62/123456` | priority | 美国临时申请，常用作优先权 |
| 纯数字 + B/C kind | `US 12345678 B2` | grant | 授权公开号 |
| 纯数字 + A kind | `US 12345678 A1` | publication | 申请公开号 |
| 纯数字无 kind | `US 12345678` | unknown | 本地无法区分申请/公开，须回官方库 |

confidence：结构明确且带 kind 为 high；结构明确但缺 kind 为 medium；无法判定为 low。`notes` 逐条写明判定依据；无法判定时写 `unknown`，不回退猜测。

## 2. 号码抽取（extract）

仅做候选抽取，`confidence` 沿用 normalize 结果。抽取命中须逐条人工核对：扫描件 OCR 可能粘连（尤其 WO 换行合并），不把 OCR 文本当官方著录项。

## 3. 同族归并（family）

- 依据：共用规范化后的**优先权号**；无优先权号时用**申请号**。
- 输出：`members`、`family_type`、`basis`（连接依据号码）、`exhaustive:false`。
- `family_type`：
  - `simple` — 所有成员共享同一优先权号；
  - `extended` — 经优先权号链连接（至少两跳）；
  - `observed` — 仅观测到一跳链接或证据不足。
- **始终 `exhaustive:false`**：输入语料有限，未观测到 ≠ 不存在；官方同族以 PATENTSCOPE/Espacenet 登记为准，此处只报告观测链接。

## 4. 法律状态核验

- 必记字段：`status`、`effective_date`、`source`、`as_of`、`staleness_risk`、`confidence`。
- 官方当前状态未知 → 写 `unknown`，`staleness_risk:true`，不推断"有效"。
- 不把转载/聚合页当登记依据；官方库与二手来源冲突时并列记录并标 `source_conflict`。
- 官方登记源：USPTO Patent Center / PATENTSCOPE / Espacenet / 各国局（按案例可用授权选择）。

## 5. 授权与展示分级

| 层级 | 条件 | 可展示字段 |
|---|---|---|
| 绿 | 官方公开登记或授权可公开数据 | 公开号、申请人、IPC/CPC、权利要求公开文本、官方法律状态 |
| 黄 | 聚合库/二手来源，许可允许摘要 | 著录项摘要，须标注来源与查询日期 |
| 红 | 付费墙/需登录/保密 | 不抓取、不登录、不展示原文，只记 limitation 与降级方案 |

## 6. 确定性保证

脚本驱动的字段（normalize/extract/family/scaffold 的骨架字段）对同一输入逐字节一致；`canonical` 子命令在比较时剔除 `runtime` 时间戳。Agent 写入的结论类字段须以来源逐字 quote 为依据，不写依赖模型随机性的概括句。
