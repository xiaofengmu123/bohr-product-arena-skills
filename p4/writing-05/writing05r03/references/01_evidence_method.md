# 证据方法规范（01_evidence_method.md）

本文件是 Skill 的判定口径，Agent 与复核人都以此为准。所有示例均为**示意**，不冒充正式案例结果。

## 1. 论断切分规则

把目标稿件拆成**可判定**的论断单元，而不是整段复述。一条论断 = 一个可判定真假的实质主张。

- 一条论断只主张一件事；复合句拆成多条。
- 数字、术语、因果、比较、推荐、引用各自成条，便于逐条核验。
- 论断必须用**肯定句**重述（即便原文是问句/祈使句），并去掉冗余修饰。
- 编号 `C001…Cnnn` 连续、稳定；同一稿件的编号在两次运行中不变。
- 切分后人工复核：有无遗漏的真实论断、有无把解释性语句误当事实主张。

论断类型（`claim_type`）：`factual`（事实/数值）、`inference`（推断）、`comparison`（比较）、`causal`（因果）、`citation`（引用主张）、`recommendation`（建议/推荐）。因果类强制要求 `hedge_required`。

## 2. 证据强度分级（`strength`）

| 级别 | 含义 | 判定依据 |
|---|---|---|
| L1 | 共识 / 多中心 RCT / 系统综述或 Meta 分析 / 权威指南 | 多来源一致、明确的方法学证据 |
| L2 | 单篇 RCT / 大样本前瞻队列 / 高质量注册研究 | 单来源但研究设计较强 |
| L3 | 观察性 / 病例对照 / 小样本 / 单中心 / 回顾性 | 设计或样本量受限 |
| L4 | 预印本 / 会议摘要 / 专家意见 / 综述二手转述 | 未经同行评议或为转述 |
| U | 证据强度无法判定或材料不足 | 缺方法学信息、仅摘要、不可读 |

等级**向下兼容**：证据支持的范围只到该等级能支持的程度。`strength_reason` 必须写明"为什么是这一级"（研究设计、样本量、重复验证、共识程度之一即可），不得只写等级名。

## 3. 支撑程度（`support_level`）与结论（`verdict`）

- `fully`：原文直接、明确支持该论断，范围与强度相称 → `supported`
- `partially`：原文支持相近但更弱/更窄的表述 → `partial`（正文必须收窄或加限定）
- `weakly`：仅有间接、弱关联 → `weak`
- `conflict`：多来源结论冲突，必须两个都记录并标注 → `conflict`
- `none`：未找到任何支撑 → `unsupported`（正文删除或改为 `pending_human`）

一条论断可对应多条证据（多篇文献）；一篇文献可支撑多条论断。冲突不隐藏：把对立证据一并写入 `support`，并标 `conflict`。

## 4. 证据类型（`evidence_type`）

`primary`（原始研究）、`review`（综述）、`guideline`（指南）、`preprint`（预印本）、`expert_opinion`（专家意见）、`unknown`（无法判定）。区分来源类型，正文表述与之一致：预印本不可写成已见刊结论，指南不可与单篇观察等同。

## 5. 不确定性表述（`uncertainty`）

需写限定语的情形与措辞原则：

| 情形 | 处理 |
|---|---|
| 相关性被写成因果 | 改为"相关/关联"，保留"因果需进一步验证" |
| 单篇小样本写成领域共识 | 收窄为"该研究提示/在……人群中" |
| 冲突证据被隐藏 | 并列呈现，标 `conflict` |
| 机制未明 | "机制尚不清楚"，不做机制推断 |
| 适用范围被扩大 | 写明人群、条件、随访期限等边界 |

既不夸大（把弱证据写成强结论），也不含糊到无法判断（"可能有一定作用"而无方向、无大小、无条件）。

## 6. 定位与摘录规范（locator + quote）

- locator 统一格式：`{source_doc}#{unit}`，unit 为 `Sec{节}.P{段}`、`P{段}`、`p{页}`、`L{行}` 或字符区间；无法分节用段落/页。
- 每条证据必须有**逐字 quote**（与原文一致，不得改写、不得截断改变语义；截断用 `…` 标注）。
- 数字、术语、引用、图表、关键论断均写入 `sources`，逐条 `{key, value, kind, location, source_doc, change}` 可回溯。
- 无法回溯来源的内容**不写入 deliverable**；确需保留则记 `uncertainty` 或 `pending_human`。

## 7. 可复现性（复得现）

- 论断清单、证据表、原文摘录、判定理由四件套全部落盘。
- 编号、排序、序列化全部确定性（见 SKILL.md 第 7 节）。
- 冲突、缺口、人工确认项单独成节，不混入成稿正文。

## 8. 中间产物 schema（/app/work/analysis.json）

Agent 在 P2 落盘的中间文件，由 `scripts/main.py finalize` 归一化并组装。字段如下：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 否 | 缺省时取 case.json |
| `summary` | string | 否 | 一句任务/受众/体裁摘要 |
| `manuscript` | string | 是 | 成稿全文（可编辑 Markdown） |
| `claims` | array | 是 | 每条 `{claim_id, claim_text, claim_type, hedge_required, uncertainty}` |
| `evidence` | array | 是 | 每条 `{claim_id, source_doc, locator, quote, support_level, strength, evidence_type, strength_reason}` |
| `sources` | array | 否 | 每条 `{key, value, kind, location, source_doc, change}` |
| `uncertainty` | array | 否 | 全局不确定性说明 |
| `pending_human` | array | 否 | 人工确认项 |
| `limitations` | array | 否 | 材料缺口、异常 |

`claim_id` 可省略，finalize 按顺序补 `C001…`；重复 id 自动去重并重编号。所有枚举值越界时被 finalize 钳制为合法值（如未知 strength → `U`）。

### 切分示例（示意，非正式案例）

底稿句：*"该干预显著降低死亡率（HR 0.72），且机制上抑制了炎症通路。"*

拆成两条论断：

- `C001` 类型 `comparison`：干预组相较对照组死亡率更低（HR 0.72）。证据：原文 HR 及其置信区间；强度 L2（单篇 RCT 直接报告）。
- `C002` 类型 `causal`：机制是抑制炎症通路，且 `hedge_required=true`。证据：若原文只有相关性数据，则 `support_level=partially`，正文收窄为"与炎症标志物下降相关，因果未证实"。

## 9. 失败类型库（发现即记录，不编造）

| 失败类型 | 识别信号 | 处置 |
|---|---|---|
| 材料不可得 | 路径缺失/权限不足 | `limitations` 记录，`status=partial` |
| 仅有摘要 | 无法定位全文段落 | 只对摘要内内容出证据，标 `L4`/`U` 与 `pending_human` |
| 原文与摘要矛盾 | 摘要结论 ≠ 正文结果 | 两条都记录，标 `conflict`，正文采用正文数据 |
| 证据冲突 | 多来源结论相反 | 并列写入 support，标 `conflict`，不隐藏 |
| 二手转引 | 引用的是转述而非原文 | 标 `L4`，`pending_human` 提示核查原始文献 |
| 版本差异 | 预印本 vs 见刊版 | 标明版本，正文按所读版本表述 |
| 图表不可读 | 图片/公式无法解析 | 不猜内容，`limitations` 记录"该处有图/公式" |
| 格式不支持 | PDF 扫描件无文本层 | 尝试 OCR 或标 `U`，不强行抽取 |
| 数字无法回溯 | 找不到来源 | 不写入 deliverable，记 `uncertainty` |

失败类型库本身可复用：每次运行把新遇到的失败类型追加说明，沉淀为团队口径。
