# 保真守恒规则（preservation rules）

本文件定义 `scripts/checks.py` 背后的判定标准，供 Agent 在逐句编辑与双通道审校时遵循，也供第三方复核判定结果。所有规则均以"原文—改文"对照为单位执行，确定性、无随机、可复现。

## 1. 限定强度守恒（hedge conservation）

- 弱限定词（`may / might / could / suggest / indicate / possibly / likely / appear / seem / perhaps / probably / preliminary / 可能 / 或许 / 似乎 / 大概 / 约 / 提示 / 倾向于 / 初步 / 有待 / 尚需` 等）在改文中消失，同时结论动词或断言仍在，视为**疑似加强结论**，必须处理。
- 强结论动词（`demonstrate / prove / establish / confirm / verify / definitely / clearly / 证实 / 证明 / 确立 / 确认 / 无疑 / 必然` 等）在原文中不存在、改文中出现，视为**疑似加强结论**，必须处理。
- 处理方式只有两种：① 回退该处改写，恢复原限定词；② 在该 unit 的 `hedge_note` 写明"原限定词由某同义限定词替代/保留"的理由。`verify` 读到非空 `hedge_note` 即清除警告；没有理由的静默增强/削弱一律记 `warning`，最终进入 `version_boundary` 的 `pending_human`。

## 2. 数字与单位守恒

- 带单位或统计上下文的数字（`400 pg/mL`、`37 °C`、`p < 0.05`、`n = 32`、`95% CI`）在改文中丢失或新增，记为 `error`，必须清零。
- 普通数字（无单位）多重集发生变化，记为 `warning`，需在 `reason` 说明是否因合并/拆分句子所致；不说明则保留警告。

## 3. 引用与图表指向守恒

- 引用标记（`[3]`、`[3, 5]`、`[3-7]`、`(Smith, 2020)`）与图表指向（`Fig. 1`、`Table 2`、`Eq. (1)`、`图1`、`表2`、`公式(1)`）在改文中丢失或新增，记为 `error`。
- 编号本身不得重排、不得改写为他人作品；原文没有的引用不得补造。

## 4. 术语与缩写一致性

- 同一概念在全文只保留一个规范写法（`glossary.json` 的 `canonical`）；其余 `variants` 在改文中出现记 `warning`。
- 缩写首次出现必须伴随全称；缩写单独出现而无全称记 `warning`（首现定位记入 `first_use_locator`）。
- 中英双语时，术语表需给出中英对齐，改文两语保持一致。

## 5. 覆盖与溯源

- 底稿的每个句子（≥10 字符）必须被至少一个 edit unit 的 `original` 覆盖，否则记 `error`（`sentence_coverage`）。
- 凡 unit 的原文含数字/引用/图表指向，必须在该 unit 的 `evidence` 中给出至少一条 `{fact, locator, quote}`，`quote` 为材料中逐字片段；缺失记 `error`（`evidence_traceability`）。

## 6. 冲突与缺口

- 两个来源不一致时，两边都记录并标 `source_conflict`，不静默选边。
- 材料缺失写 `MATERIAL GAP`、`not_reported` 或 `pending_human`，不编造数据、分数、引用或 DOI。
