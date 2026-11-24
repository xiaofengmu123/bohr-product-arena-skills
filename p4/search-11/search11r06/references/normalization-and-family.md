# 号码规范化、同族归并与核验规则

本文件定义检索各环节的字段口径与判定规则，供 Agent 与评审者复跑核对。
所有规则对应 `scripts/core.py` 中的实现；规则与实现不一致时以本文件为准并修正实现。

## 1. 号码类型口径（先定类型，再谈规范化）

| 类型 | 英文 | 典型形态 | 说明 |
|-|-|-|-|
| 公开号 | publication | `US 2020/0123456 A1`、`WO 2020/123456 A1` | 公开/申请公布文献号，kind code 常为 A 系 |
| 授权号/专利号 | grant | `US 10,000,000 B2`、`US 12345678 C1` | kind code 常为 B/C/U/Y 系 |
| 申请号 | application | `US 16/123,456`、`CN 201810123456.7` | 含斜杠或小数点分隔，未必带 kind code |
| 优先权号 | priority | `US 62/123,456`（临时申请） | 常与申请号同格式，需上下文确认 |

**规则 1.1**：`publication` 与 `grant` 是同一件专利生命周期的不同号码，不是两件专利；
检索时必须同时记录两者的 raw 与 normalized，避免"公开号=授权号"的混用。

**规则 1.2**：优先权号与申请号格式相近，仅在材料明确标注"优先权/Priority"时才标
`priority`；否则标 `application` 并在 `notes` 写 `possibly_provisional`，不硬猜。

## 2. 规范化规则（canonical form）

- 国家/组织代码取两位大写（`US/EP/WO/CN/JP/KR/…`）；`PCT/` 前缀视为 `WO`。
- 去除逗号、空格、全角字符、零宽字符；保留斜杠 `/` 与小数点 `.`（用于区分申请号格式）。
- kind code 从尾部剥离（`A1/B2/U/…`），进入 `kind_code` 字段。
- 规范化结果 `normalized` = `{COUNTRY}{NUMBER}{KIND}`，如 `US12345678B2`、
  `WO2020123456A1`、`US16/123456`。
- **规则 2.1**：规范化是"确定性归一"，不是"证明号码真实存在"。真实存在性须回官方库核验，
  核验结果写入 `patent_records[i].verified` 与 `sources[]`。

## 3. OCR 边界处理（WO/扫描件合并边界）

- 扫描件 OCR 可能把相邻号码粘连、把分隔符（斜杠/空格）吃掉，或把行尾行首拼在一起。
- `extract` 只产出**候选 + 位置 span + `ocr_uncertain` 标记**，绝不静默合并或拆分。
- **规则 3.1**：`ocr_uncertain=true` 的候选必须先回官方库按原文逐字符核对，再进入
  `patent_records`；无法核对时保留 `ocr_uncertain` 并写入 `uncertainty[]`
  （`ocr_uncertain`），不得当成确定号码使用。

## 4. 同族归并口径

| 口径 | 定义 | 本 Skill 处理 |
|-|-|-|
| simple family | 同一优先权号集合 | 仅当材料同时给出优先权号时才可标 `simple`，否则不标 |
| extended family | INPADOC 传递链接的全量族 | 需要外部数据库；本 Skill 不伪造，标 `not_reported` |
| observed family | 仅由当前语料可见的申请号/优先权号连接 | **默认口径**，`family_type=observed`，`exhaustive=false` |

- **规则 4.1**：归并键为**归一化后的申请号或优先权号**（去除非字母数字字符后比对），
  做并查集传递合并；两个记录共享任一键即同族。
- **规则 4.2**：`exhaustive=false` 是强制项——当前语料看不到的连接不代表不存在，
  必须显式声明"非穷尽"，避免把 observed 族当成完整族。
- **规则 4.3**：同族成员的权利要求范围可能不同（family 不等价），不得因同族而互相
  替用权利要求；逐成员记录各自的权利要求原文。

## 5. 法律状态核验

- **规则 5.1**：法律状态只接受**官方登记来源**（USPTO/Espacenet/PATENTSCOPE/国家局），
  转载页、二手聚合站、几年前的博客不得作为登记依据。
- **规则 5.2**：官方当前状态未知时，`status=unknown` 并写 `staleness_risk`，
  **不得推断"有效/在审/失效"**；同时记录 `as_of`（查询日期）与 `source`。
- **规则 5.3**：多来源状态冲突时，双方证据并列，`source_conflict=true`，
  写 `pending_human`，不静默择一。
- **规则 5.4**：状态字段固定为 `status / effective_date / source / as_of /
  staleness_risk / confidence`，缺一项写 `not_reported`。

## 6. 授权边界分级

| 级别 | 含义 | 处理 |
|-|-|-|
| green | 官方公开登记/已授权公开元数据 | 可记录库名、版本、查询日期、字段 |
| yellow | 出版商/预印本元数据 | 可记录元数据与 URL，注明版本/同行评议状态 |
| red | 需登录商业库/付费全文/受限数据 | 不登录、不抓取、不展示原文；写 `degradations` 与降级方案 |

- **规则 6.1**：每条 `authorization[]` 记录 `source / license_basis / tier /
  displayable_fields / retention`；red 级只记录"不可用与降级方案"，不记录任何正文。

## 7. 证据溯源

- **规则 7.1**：每个关键结论（号码、同族关系、法律状态、文献相关性）必须有
  `locator`（材料内定位，如 `materials/patent.txt#p3`）与逐字 `quote`。
- **规则 7.2**：`quote` 必须与材料原文逐字一致；OCR 粘连处引用 OCR 原文并附
  `ocr_uncertain` 说明，不"脑补"干净文本。
- **规则 7.3**：冲突信息并列记录并标 `source_conflict`，证据图
  （`evidence_map.json`）单独列出冲突清单供复核。
