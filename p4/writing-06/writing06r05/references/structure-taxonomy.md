# 结构问题分类与标注规范

本文件是六维结构诊断的统一分类、判据与标注模板，供模型在执行阶段与作者复核时共用。所有输出必须引用本文定义的动作与定位格式，避免各案例口径不一。

## 0. 定位格式（locator）

定位必须指到"文件 + 章节 + 段落 + 行号"，可被作者直接打开核对：

- 纯文本/文本稿件：`文件名#S节号[P段号][@L起始行]`
  - 例：`manuscript.md#S3P2@L41` = 第 3 节第 2 段、第 41 行起。
- PDF 提取文本：沿用页码/段落，例：`paper.pdf#p4¶2`；若用 pdftotext 输出 `.txt`，则按 `paper.txt#S…` 处理。
- 图/表：`图 2`、`Table 1` 作为 source_ref，正文定位仍用所在段落 locator。

每条判断同时给出 **locator**（定位）与 **quote**（逐字原文，≤400 字）。quote 必须原样复制材料，不得改写、不得增删字词；材料不支持时写 `not_reported`，不得补造。

## 1. 六维分类、判据与编辑动作

每个问题条目统一为：`{id, dimension, locator, quote, verdict, reason, edit_action}`。维度取值见下；edit_action 必须落在受控动作集 `{move, merge, split, narrow, reorder, insert_transition, reassign_evidence, add_limitation, insert, delete, replace, clarify}`。

### 维度 1：研究问题与贡献主张对齐（question–contribution）
- 判据：研究问题是否被明确提出并贯穿全文；结论中的贡献主张是否与实际完成的工作、方法与数据范围一致；问题、方法、结果是否各说各话。
- 证据信号：引言问题句缺失/漂移；方法回答不了引言问题；结论声称的贡献超出结果。
- 可执行动作：`insert`（补问题句）、`clarify`（重述问题）、`narrow`（收窄贡献主张）、`reorder`（把问题前置/后置）。

### 维度 2：章节结构与叙事顺序（chapter–order）
- 判据：章节顺序、篇幅分配、信息释放顺序是否服务论证；该前置的铺垫、该后移的细节、该合并的章节是否错位。
- 证据信号：方法细节在引言堆积；结果前插入过长背景；讨论与结果比例失衡；相邻章节功能重叠。
- 可执行动作：`move`、`merge`、`split`、`reorder`、`delete`（删冗余细节）。

### 维度 3：论证链与论据对应（claim–evidence）
- 判据：逐条抽取论断，找其支撑论据；找断裂、跳跃、循环论证、论据强度不足以支撑论断的位置。
- 证据信号：结论句无对应数据/引用；用 A 数据支撑 B 论断；以引文代论证；因果与相关性混用。
- 可执行动作：`reassign_evidence`（换/补论据）、`insert`（补证据）、`narrow`（收窄论断）、`add_limitation`。

### 维度 4：段落关系与重复内容（paragraph–redundancy）
- 判据：同义重复、跨章节重复表述、段落间缺过渡或逻辑跳跃。
- 证据信号：两句/两段语义近似；相邻段无连接词、主语突转；同一表述在引言与讨论重复出现。
- 可执行动作：`merge`、`split`、`delete`、`insert_transition`。

### 维度 5：结论边界（conclusion–boundary）
- 判据：结论是否超出数据与实验条件允许的范围；是否遗漏必要限制；是否把相关性表述成因果。
- 证据信号：见下方"相关性→因果"动词表；结论忽略样本量/条件/外推限制。
- 可执行动作：`narrow`、`add_limitation`、`clarify`、`replace`（换动词）。

### 维度 6：表述尺度（expression–scale）
- 判据：量化表述、显著性、方向与幅度的语气是否与证据强度匹配；是否用绝对化/宣传式措辞。
- 证据信号：无 p 值却称"显著"；单研究称"证明/首次"；无效应量却称"大幅"。
- 可执行动作：`replace`、`narrow`、`clarify`。

## 2. 结论边界：相关性→因果动词对照（结论边界检查）

| 证据强度 | 可用表述 | 应避免表述 |
|-|-|-|
| 横断面/相关性 | 相关、与…相关联、associated with | 导致、引起、促进、causes、leads to |
| 时序/纵向 | 先于、预测、predicts | 直接证明因果 |
| 干预/随机对照 | 表明、支持因果、demonstrates | 过度外推到其他人群/条件 |
| 单研究/小样本 | 初步表明、提示 | 证明、证实、首次、完全 |

每条结论先查此表，再决定是否用 `narrow`/`replace`/`add_limitation` 动作。数据范围、样本条件、测量手段的限制必须落在 `version_boundary` 与 `pending_human`。

## 3. 论断—论据映射表模板

论证链维度（维度 3）统一用下表逐条抽取，写入 result.json 的 `source_map`/`sources`：

| claim_id | 论断（逐字 quote） | locator | 论据（locator + quote） | 强度 | 缺口 |
|-|-|-|-|-|-|
| C1 | "因此 X 导致 Y。" | §5P1@L12 | 结果§3 只有相关图 | 不足 | 缺干预设计 |

- 论断与论据都要有 locator + quote；找不到论据时论据列写 `not_reported`，并进入 `uncertainty`/`pending_human`。
- 冲突信息不隐藏：同一指标两处不一致时两条都记录并标 `source_conflict`。

## 4. 不把风格偏好当结构错误

以下不属于结构问题，不得写入 diagnosis 或 edit_plan：

- 单纯词汇偏好、句式长短、用词雅俗（除非改变论证强度或逻辑关系）。
- 与论证无关的标点、格式微调（除非格式约束明确要求）。
- 作者学科惯例中的术语差异。

遇到疑似条目时，先问"它是否改变论证链或结论边界"；不改变则删除或降级为 `pending_human`。
