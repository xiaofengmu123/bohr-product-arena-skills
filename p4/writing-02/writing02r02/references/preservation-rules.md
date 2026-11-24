# 保真规则：润色中不可改变的东西

本文件是 `scripts/checks.py` 所执行规则的文字说明。Agent 在逐句编辑（阶段 2）前应先读本文；`verify` 报告中的每条 error/warning 都能在此找到对应口径。

## 1. 数值（error 级）

- 原文中的每个数值（含小数、千分位逗号、百分数、n、P 值）必须原样出现在改文中；`50`、`50.0`、`050` 视为同一数值。
- 若确需修正数值（例如材料内两个来源冲突），不得静默修改：必须写 `edit_type=correction`，`reason` 说明依据，并把该事实放入 `version_boundary` 标 `pending_human`；同时在 `evidence.note` 写 `source_conflict`。
- 新增数值必须能从材料逐字溯源，否则标 `not_reported`，不得杜撰。

## 2. 单位（warning 级）

- `%`、`‰`、`°C`、`℃`、`mg`、`mL`、`kDa`、`MPa`、`mM` 等（清单见 `checks.py` 的 `UNIT_TOKENS`）在改文中不得消失。
- 把缩写展开成全拼（如 `mg` → `milligrams`）或反过来，属表达调整，允许，但必须在 `reason` 写明；否则记 `unit_missing` 警告。
- 改数值单位（如 `mg` → `μg`）属事实变更，按第 1 条 error 处理。

## 3. 引用与图表指向（error 级）

- 引用编号 `[1]`、`[2,3]`、`[7-9]`、`(Smith, 2020)` 的集合必须与原文一致；新增引用必须溯源；同组多个编号的顺序变化记 `citation_reordered` 警告。
- 图表指向 `Figure 1`、`Table 2`、`图1`、`表2` 的编号必须原样保留；改指向即改事实。

## 4. 限定强度与否定（warning 级，需逐条处置）

- 弱限定词（`may`/`might`/`suggest`/`可能`/`提示`/`有望`/`初步`…）与强词（`prove`/`demonstrate`/`confirm`/`证实`/`表明`…）不可互换：**加强或削弱结论都属改动**，需回退或写理由。
- 否定标记（`not`/`without`/`不`/`未`…）不可增删。
- 因果（because/cause/导致/由于）与相关（associated/correlated/相关/关联）不可混用；条件句、量词（all/some/most/全部/部分）保持原范围。
- `verify` 对这些只报 warning，因为判断语义需要模型；Agent 必须对每条 warning 给出"回退"或"显式理由"，并写入 `version_boundary`。

## 5. 术语一致性（warning/error 级）

- 每个概念全文只有一个规范写法（`canonical`），缩写首次出现须给全称（如 "artificial intelligence (AI)"）。
- 术语表内同一 `canonical` 出现两次记 `glossary_conflict`（error）；改文仍用已弃用的变体记 `term_variant_used`（warning）。

## 6. 覆盖与溯源（error/warning 级）

- 每个原文句子在 `units[]` 中至少有一个对应 unit（合并/拆分须在 `reason` 说明）。
- 含数字/引用/图表指向的句子必须有 `evidence.locator + evidence.quote`，否则记 `missing_evidence`（error）。
- 每个 unit 必须有 `original`、`revised`、`edit_type`、`reason`，缺一记 error。
- 材料缺失写 `MATERIAL GAP`/`pending_human`/`not_reported`，不补造。

## 7. 双通道审校（阶段 3 的模型动作，脚本无法替代）

1. **回读**：把改文逐句读一遍，确认与原句主张一致。
2. **回译**：中英互译任务把改文回译成源语言，与原文比对主张与限定词。
3. 两者与 `verify` 的 warning 合并成同一份待处置清单，逐条"回退或写理由"。
