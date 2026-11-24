# 保真规则（Preservation Rules）

润色的唯一硬约束：**只改表达，不改事实与强度**。本文件是模型在阶段 2–4
（逐句编辑、双通道审校、修复）必须逐条遵守的判定标准。

## 1. 必须原样保留（任何改动都是 error 级）

| 类别 | 例子 | 校验方式 |
|---|---|---|
| 数字与单位 | `98.3%`、`1.5 MPa`、`25°C`、`p < 0.05` | `checks.extract_measurements` 前后 multiset 相等 |
| 引用编号 | `[1]`、`[3,7]`、`(Smith et al., 2020)` | `checks.extract_citations` 前后相等 |
| 图表指向 | `Fig. 1`、`图 1`、`Table 2`、`式(3)` | `checks.extract_figrefs` 前后相等 |
| 限定词强度 | `可能`/`或许`/`suggest` vs `证实`/`demonstrate` | `checks.extract_hedges` 不允许静默升降 |
| 因果 vs 相关 | "与…相关"≠"导致"；"associated with"≠"causes" | 人工复核 + hedge 检查 |
| 否定范围 | "并未显示显著差异"≠"显示无差异" | 人工复核 |
| 条件句 | "若…则…"的假设前提不变 | 人工复核 |
| 量词 | "多数/部分/全部/三组中两组" | 人工复核 |

## 2. 允许改（且应该改）

- 语法：主谓一致、时态、冠词、介词、单复数、悬垂修饰、标点。
- 句法：长句切分、冗余名词化、直译腔、逻辑连接词滥用。
- 语域：按体裁（期刊/学位论文/基金/审稿回复/英文摘要）统一语气。
- 术语：同义混用归一，缩写首次出现给全称，大小写与连字符统一。
- 中英表达：主语与信息重心重排，消除中式英语。

## 3. 强度守恒（分级动词）

| 强度 | 英文 | 中文 |
|---|---|---|
| 强（已证实） | demonstrate, confirm, prove, establish | 证实、证明、确立、显示、表明 |
| 弱（初步/推断） | suggest, indicate, may, could, likely | 初步表明、可能、或许、似乎、有望、提示 |

规则：原文用强动词→改文不得降为弱；原文用弱动词→改文不得升为强。
确需改变强度的，必须在 `reason` 写明依据，并记入 `version_boundary`
的 `human_confirmation` 项。

## 4. 术语一致性

- 同一概念全文唯一写法（中英、缩写、大小写、连字符一致）。
- 缩写首次出现给全称：`artificial intelligence (AI)`；中文同理。
- 术语表建在 `/app/work/glossary.json`，`finalize` 落盘为 `glossary.md`。

## 5. 溯源要求

- 每个带数字/引用/图表指向/关键论断的句子，必须在
  `fact_source_map` 中有一条 `{fact, locator, quote}`。
- `locator` 形如 `draft.md#p2.s0`（文件 + 段落 + 句序）。
- `quote` 必须是材料中的逐字片段，不是转述。
- 材料缺失写 `MATERIAL GAP`，冲突写 `source_conflict`，缺字段写
  `not_reported`/`pending_human`，绝不编造。

## 6. 修改记录格式（逐句）

每条 unit 必须含：

```json
{
  "id": 1,
  "locator": "draft.md#p0.s0",
  "original": "原文逐字",
  "revised": "改文逐字",
  "edit_type": "grammar | terminology | register | hedge | clarity | no_change",
  "reason": "为什么改、为什么没改坏强度",
  "evidence": {"locator": "draft.md#p0.s0", "quote": "原文逐字"}
}
```

第三方应能仅凭 `original`/`revised`/`edit_type`/`reason` 判断"这处该不该改"。
