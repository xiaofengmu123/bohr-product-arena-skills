# 保真规则与检查清单（精简版）

本文档定义润色阶段的硬约束与校验口径，供 Agent 编辑时逐条遵守，也供
`scripts/checks.py` 做确定性复核。原则只有一句：**只改表达，不改事实与强度。**

## 1. 必须原样保留（硬约束）

以下内容在 `revised` 中必须逐字出现，改动即事实错误：

1. **数字与单位**：`37.5%`、`p < 0.05`、`25 °C`、`3.2 mM`、`n = 120`。
   数字不得四舍五入、不得换成百分比/倍数等价表述，单位不得替换。
2. **引用编号与文献**：`[1]`、`[1,2]`、`[3–5]`、`(Wang, 2020)` 等。
3. **图表与公式指向**：`Fig. 1`、`Table 2`、`图1`、`式(3)`、`Eq. (4)`。
4. **限定词强度**：`可能/may/might/could`、`似乎/appears`、`初步/preliminary`
   等弱化词不得删除；`证明/prove/demonstrate/confirm` 等强化词不得新增。
5. **因果与相关**：`与…相关` 不得改成 `导致`；`correlated with` 不得改成
   `causes`；否定范围与条件句（`if`/`除非`）保持原样。
6. **量词与范围**：`多数`、`部分`、`约`、`大于`、`不超过` 不得改动。

## 2. 允许修改（表达层）

- 语法、主谓一致、时态语态、冠词介词、标点、悬垂修饰、长句切分。
- 术语统一（按术语表规范写法）、缩写首次出现给全称、大小写与连字符。
- 消除口语化、宣传化、绝对化表述与人称误用，贴合目标文体语域。
- 中式英语、直译腔、冗余名词化、逻辑连接词滥用，以及中译英的信息重心。

## 3. 每句产出结构（写入 `edits.json` 的 `units[]`）

```json
{
  "id": 1,
  "locator": "manuscript.md#L1",
  "original": "原文",
  "revised": "改文",
  "edit_type": "grammar|terminology|register|hedge|bilingual|structure|no_change",
  "reason": "为什么改/为什么不改",
  "facts": [{"kind": "fact-bearing", "locator": "...", "quote": "...", "tokens": {}}],
  "evidence": [{"locator": "...", "quote": "..."}],
  "status": "done|pending"
}
```

- 不改的句子也要给 `revised = original`，`edit_type = no_change`，理由写
  `already correct`，保证覆盖完整。
- 含数字/引用/图表指向/关键论断的句子必须带 `evidence`（locator + 逐字
  quote），否则 `verify` 报 provenance error。

## 4. 校验口径（`scripts/checks.py`）

| 检查 | 级别 | 含义 |
|---|---|---|
| coverage | error | original 非空而 revised 为空，且未标 no_change |
| number_unit | error | 原文数字(+单位)在改文中缺失 |
| citation_ref | error | 原文引用/图表指向在改文中缺失 |
| provenance | error | 事实句缺少 evidence 溯源 |
| hedge_dropped | warning | 弱化限定词消失；回退或写理由 |
| claim_strengthened | warning | 强化词新增；回退或写理由 |
| terminology | warning | 改文使用了非规范术语变体 |

`errors` 必须清零；每个 `warning` 必须"回退"或在 `reason` 显式写明保留理由。

## 5. 提交前自检清单

- [ ] `result.json` 可解析，`case_id` 非空，`status` 合法。
- [ ] `output_contract` 全部键回填，未知键未删。
- [ ] 交付"润色全文 + 逐句修改表"，不是建议或整段重写。
- [ ] `verify` 报 `errors == 0`；每个 warning 已回退或写明理由。
- [ ] 术语表规范写法全篇一致，缩写首次出现给全称。
- [ ] `fact_source_map` 每条含 `fact` + `locator` + 逐字 `quote`。
- [ ] 缺口写 `pending_human` / `not_reported` / `source_conflict`，不编造。
- [ ] `scripts/selftest.py` 三种场景全部 PASS。
