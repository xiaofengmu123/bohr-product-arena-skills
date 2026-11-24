# 号码规范化、同族归并与证据强度规则

本文件是 `scripts/main.py` / `scripts/patentlib.py` 的规则说明，供 Agent 复核脚本行为与手写回填。

## 1. 号码规范化：多解释而非单值

专利号在不同国家/阶段有不同格式。规范化**不追求唯一答案**，而是返回全部候选解释并给置信度，由证据（来源标签、上下文）决定采纳哪个；无法决定时保留多个并标 `uncertainty`。

| 示例 | number_type | 说明 |
|-|-|-|
| `US 12,345,678 B2` | grant_publication | 授权公告号（B 类种类代码） |
| `US 2008/0123456 A1` | publication | 申请公开号（A 类，含公开年份） |
| `US 12/345,678` | application | 申请号（系列号+序号） |
| `US 60/123,456` | provisional | 临时申请号（60/61/62 系列） |
| `WO 2008/123456 A1` | international_publication | 国际公开号 |

**口径硬规则**：公开号 ≠ 授权号；优先权号 ≠ 申请日。三者分开记录字段，不互相替代。种类代码按 WIPO ST.16：`A` 申请公开、`B` 授权公告、`U/Y` 实用新型、`S` 外观、`C` 授权后修改、`E` 再颁、`P` 植物、`T` 防御性公开。

脚本输出字段：`raw`（原样）、`interpretations[]`（候选解释，含 `number_type`、`kind_code`、`year`、`number`、`series`、`confidence`、`key`）、`best`、`ambiguous`。当 `ambiguous=true` 时，采纳哪个解释必须由来源上下文决定，禁止随手取第一个。

## 2. 同族归并：只认共用号码，绝不认主题相似

**归并唯一依据**：两记录共用同一个申请号（同一司法辖区）或同一个优先权号。共用号码由脚本在申请号/优先权号空间内做精确匹配。

**绝不**仅因关键词命中、分类号相近、申请人相同或主题相似就归为同族——这正是题目点名的错误。若发生这种"候选合并"被否决，写入 `merge_rejected`，记 `a`/`b` 与 `reason`。

**三个显式口径**：

- `family_type` 一律标 `observed`（观测族）：只反映给定语料内观测到的链接，不声称完整。
- `exhaustive` 一律 `false`：给定语料不足以覆盖该申请的全部同族成员。
- `family_basis` 说明归并依据：`shared_application_or_priority_number`（共用号码）、`singleton`（未发现链接）。无共用号码时不得凭空归并。

**限制**：授权号（`US 12,345,678 B2`）与申请号（`US 12/345,678`）处于不同号码空间，脚本**不会**凭号码形态自动推断二者同源；除非来源明示二者属于同一申请，否则视为两条独立记录并标 `pending_human`。

## 3. 证据强度：每条结论可指回原文

每条 `evidence` 必须四要素齐全：`locator`（来源定位，如 `materials/record-1.txt#claim1` 或 `USPTO 页面 URL`）、`quote`（逐字引用，不得改写）、`strength`（证据强度）、`about`（支撑的结论）。

`strength` 四级口径：

| strength | 含义 |
|-|-|
| `direct_quote` | 从来源逐字引用，直接支持结论 |
| `derived` | 由原文可机械推导（如规范化、去重），非原文直陈 |
| `asserted` | 来源声称但未逐字核验（如二手聚合页摘要） |
| `unverified` | 未经来源核验的推断/待验证假设 |

**严禁** `unverified` 条目冒充事实结论；`unverified` 必须同步出现在 `uncertainty` 或 `pending_human`。

## 4. 法律状态：默认 unknown，登记簿才是依据

法律状态唯一可信依据是官方登记簿（USPTO Patent Center / Espacenet / PATENTSCOPE / 国家局）。二手转载页、若干年前的缓存页不构成登记依据。

- 未查官方登记簿时，`legal_status.status = "unknown"`，`determination = "not_queried_official_register"`，不得推断"有效/失效"。
- 已查则逐项写 `status`、`effective_date`、`source`、`as_of`（查询日期）、`staleness_risk`、`confidence`。
- 两来源冲突时并列记录并标 `source_conflict`，写 `pending_human`，不静默择一。

## 5. 授权分级（绿/黄/红）与降级阶梯

| 层 | 定义 | 能力 |
|-|-|-|
| green | 已核验可用且有展示许可的公开源 | 全文/著录项可检索并展示 |
| yellow | 可用但许可受限或需登录 | 仅记录元数据与定位，不展示受限原文 |
| red | 付费墙/受限库/禁止展示 | 不登录、不绕过、不展示；记 degradation |

`degradation_tier` 取实际达到的最坏层（green→yellow→red 只升不降）。每次降级写 `degradations[]`（`item` + `reason`），并在 `limitations[]` 说明降级后的能力边界。
