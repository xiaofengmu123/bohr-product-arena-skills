# 保真守恒规则（preservation-rules）

本文件说明 `scripts/checks.py` 的校验口径与解决方式。它是确定性脚本，`scaffold` 冻结基线、`verify` 做守恒差集。

## 1. 为什么是“冻结基线”而不是“事后重扫”

重扫方案有一个隐蔽缺陷：某句被改得面目全非后，若校验器也从改文里找不到那个数字，就“原文丢了、校验器也看不见”，静默丢失。冻结基线方案在 `scaffold` 阶段就把原文逐句特征（数字/单位/引用/图表/限定词）连同 locator 和逐字 quote 写进 `register.json`，`verify` 只对改文重新提取特征，再与冻结基线做逐句差集。只要校验器能在原文里看到该数字，它就必然参与比对。

## 2. 特征分类与风险等级

| 类别 | 等级 | 处理 | 说明 |
|---|---|---|---|
| numbers（数字） | error | 必须回退 | 含小数、百分比、科学计数、区间 |
| units（单位） | error | 必须回退 | %、°C、mg、mL、mM、mg/mL、×g、cfu/mL 等 |
| citations（引用） | error | 必须回退 | `[1]`、`[1,2]`、`[3–5]`、`(Smith et al., 2020)` |
| figures（图表指向） | error | 必须回退 | Fig. 1、Table 2、Scheme 3、Eq. (4)、图 1、表 2、式 3 |
| hedges（限定词强度） | error→warning | 回退或写理由 | 删除限定词=加强结论；`hedge_justified`/`reason` 显式说明后降为 warning |
| strong（强断言词） | error→warning | 回退或写理由 | 新增 demonstrate/prove/confirm/证实/证明 等同理 |
| negations（否定范围） | warning | 回退或写理由 | 不/未/无/非/难以/无法/not/without… |
| quantifiers（量词） | warning | 回退或写理由 | 所有/部分/多数/少数/all/some/most/few… |
| conditionals（条件句） | warning | 回退或写理由 | 若/当/除非/只要/if/when/unless… |

**“error”必须清零**：改动导致基线 token 从改文消失（dropped）或原文与 unit 的 `original` 不一致（original_mismatch）、句覆盖缺失（missing_unit）都必须修复后才能定稿。

**“warning”二选一**：要么回退到与原文一致，要么在该句 unit 的 `reason` 写明为何这样改（例如“此处‘未’为上下文省略，未改变否定范围”）。只有 numbers/units/citations/figures 之外的类别允许写理由。

## 3. 正确性口径（针对评分点）

- 限定强度只允许持平：删除 may/suggest/可能/提示/初步 或新增 prove/confirm/证实/证明 都会触发；降低结论强度（新增 hedge）记 warning，虽不致命但建议避免，以免“偷换结论”。
- 因果 vs 相关：不把 correlate/associated 改成 cause/lead to；这类词不在词表中，靠第 3 阶段的人工回读与回译把关。
- 数字与单位成对核对：`45.2%` 会同时出现在 numbers（45.2）与 units（45.2%）两处，任一处被改都会报警。
- 引用编号与图表指向：编号本身原样保留；引用/图表换位置可，编号与对象不可改。

## 4. 冲突与缺口标注

- 两个来源同一指标不一致：全部记录，标 `source_conflict`，不选边。
- 材料缺失：写 `MATERIAL GAP` / `pending_human` / `not_reported`，不编造数据、DOI、分数。
- 文体/受众不明：最小编辑，只改硬语法错误，不动限定词，`uncertainty` 标注。

## 5. 已知启发式局限

- 单字中文词（不/未/无/非/均/皆/约）用子串匹配，可能含噪声：仅当语义确变才处理并写理由。
- 作者-年份引用、复合单位、上标引用的正则覆盖有限：未识别的仍落在数字/单位/图表类中兜底，或由 Agent 在术语表阶段人工补齐。
- 数字正则不含纯中文数字（如“三次”）：建议术语表阶段将关键中文数字归一化为阿拉伯数字并记录。
