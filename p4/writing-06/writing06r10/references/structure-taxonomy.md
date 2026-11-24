# 结构问题分类与编辑动作规则

本文件是"论文结构与逻辑优化"Skill 的可复用规则集，供诊断步骤使用，也可被其他写作 Skill 引用。所有判据都要求回到原文 locator + 逐字 quote，不凭空下结论。

## 1. 六维问题分类（对应评分完整性）

每个维度诊断时回答三个问题：问题是否真实存在（quote 佐证）、严重程度（阻断论证 / 影响阅读 / 仅风格）、如何修改（编辑动作）。

| 维度键 | 名称 | 诊断要点 | 常见缺陷示例 |
|-|-|-|-|
| `research_question` | 研究问题与贡献主张对齐 | 研究问题是否明确提出、是否贯穿全文；贡献主张是否与实际完成工作一致 | 问题与方法各说各话；贡献写"首次"但方法为常规复现 |
| `chapter_structure` | 章节结构与叙事顺序 | 章节顺序、篇幅分配、信息释放顺序是否服务于论证 | 该前置的铺垫后置；细节前置打断主线；章节可合并 |
| `argument_chain` | 论证链与论据对应 | 逐条论断是否有对应论据；是否断裂、跳跃、循环论证、论据不足 | 结果段断言机制但方法未测；用 A 数据支撑 B 结论 |
| `paragraph_relations` | 段落关系 | 段落间过渡、衔接、逻辑跳跃 | 相邻段落无过渡；段落顺序颠倒 |
| `redundancy` | 重复内容 | 同义重复、跨章节重复表述 | 引言与讨论整段重复；方法细节在结果段重述 |
| `conclusion_boundary` | 结论边界与表述尺度 | 结论是否超出数据与实验条件；是否遗漏限制；相关是否当因果 | 横断面相关写为因果；单一样本推广全领域 |

判定原则：只有能给出"哪一段、哪一句、是什么问题、如何改"的才算定位成功；泛化的写作原则不计入诊断。

## 2. 编辑动作词表（改得动的受控词汇）

每个 edit_plan 条目使用 `action` 之一，并给出 `target`（locator）、`from_to`（涉及两端）、`quote`、`rationale`、`proposed`（可选建议，标注为建议）、`dimension`、`confidence`、`status`。

| action | 含义 | 必需字段 |
|-|-|-|
| `move` | 移动到哪 | target、from_to(→目标位置)、rationale |
| `merge` | 与哪段合并 | target、from_to(合并对象 locator)、rationale |
| `split` | 从哪里拆分 | target、from_to(拆分后两个子段各自承载内容)、rationale |
| `narrow` | 哪句论断收窄 | target、quote、proposed(收窄后表述)、rationale |
| `add_transition` | 补过渡/衔接 | target、from_to(前后两段 locator)、rationale |
| `delete_duplicate` | 删除重复（保留哪处、删哪处） | target、from_to(保留 locator)、rationale |
| `reorder` | 重排顺序 | target、from_to(建议顺序)、rationale |
| `flag_confirm` | 无法自动判断，需作者确认 | target、quote、pending 项说明 |

`confidence` 取 `high / medium / low`；`low` 或 `flag_confirm` 必须同步写入 result.json 的 `pending_human`，给作者"采纳 / 部分采纳 / 驳回"三个选项。

## 3. 论点—论据映射（链得通）

对每条关键论断建立 `argument_map` 条目：

- `claim_id`：唯一编号（如 C1、C2）。
- `claim`：论断内容（可摘录或摘要，需指回 locator）。
- `claim_type`：`contribution`（贡献）/ `causal`（因果）/ `correlational`（相关）/ `scope`（范围）/ `descriptive`（描述）。
- `evidence_locator`：支撑论据的 locator；无则写 `none`。
- `evidence_type`：`measured`（实测）/ `computed`（计算）/ `literature`（文献）/ `hypothesis`（推测）/ `none`。
- `verdict`：`supported`（论据充分）/ `unsupported`（无支撑）/ `overreach`（越界）/ `gap`（断裂）/ `circular`（循环论证）/ `jump`（跳跃）。

闭环核对：研究问题（引言）→ 方法是否针对该问题 → 结果是否回答该问题 → 结论是否落在结果范围内，四处 cross-check 并记录 `verdict`。

## 4. 结论边界判定（相关≠因果）

以下情形判为边界问题并给出 `narrow` 动作：

- 横断面/相关性设计却用因果动词（cause/导致/提高/降低/使）。
- 单中心、单物种、单条件结论推广到更广总体，未加限制说明。
- 用显著但效应量小或样本小的结果做强断言。
- 遗漏必要的限制说明（样本量、条件范围、测量误差、外部效度）。

只有设计本身支持因果（随机对照、纵向、机制实验）时才可用因果表述；否则改为"相关/关联/提示"，或加"在……条件下""提示可能……"。

## 5. 脚本候选信号的使用边界

`parse.py` 输出的 `signals`（causal / correlation / hedge / claim / contribution）与 `duplicate_candidates` 是**确定性启发式候选**，只提供锚点，不是结论：

- 每个写入诊断的信号都必须回读原句确认语义（正则命中不代表真因果或真贡献）。
- 正则漏掉的问题由模型补记；脚本命中但语义不符的必须剔除，不写入 diagnosis。
- 重复段落对的相似度是 n-gram 近似，须人工对照两端原文确认是否同义重复后再写 `delete_duplicate` 或 `merge`。
