# 保真规则与归一化约定（preservation-rules）

本文是 `scripts/checks.py` 的规范说明。校验目标是"改表达、不改事实与强度"，因此只做**高信号、低误报**的守恒检查；不确定项作为 warning 交由 Agent 回退或写理由，而不是直接判错。

## 1. 必须原样保留的对象

| 对象 | 例子 | 归一化 | 违规等级 |
|---|---|---|---|
| 数字 | `32.4`、`1,234`、`3.2e5`、`3.2×10^5`、`0.50` | 去千分位、`×10^`→`e`、去尾零；科学计数与普通写法视为不同表示 | error |
| 单位 | `μg`/`ug`→`μg`、`mL`/`ml`→`mL`、`hr`→`h`；`M`(摩尔)≠`m`(米)、`K`≠`k` 保留大小写 | 仅合并无语义差别的同义写法 | error |
| 引用 | `[1]`、`[1, 12-14]`、`(2020)`、`(Smith, 2020)` | 去内部空白；作者表/年份字母变化视为真实变化 | error |
| 图表/公式指向 | `Fig. 1`、`Figure 1a`、`Table 2`、`Eq. (3)`、`Sect. 2` | 类别词归一（Fig/Figure→fig），编号必须一致 | error |
| 限定词 | `may`、`suggest`、`likely`、`approximately`、`at least`、`可能`、`初步`、`大约` | 屈折归一（indicates→indicate）；量化限定词仅当其紧跟数字时计 | warning（需 `hedge_note`） |
| 否定范围 | `not`、`no`、`neither…nor`、`不`、`未`、`无` | 不自动归一，靠逐句复核与回译人工把关 | 复核项 |
| 条件句/量词 | `if`、`unless`、`all`、`some`、`most` | 同上 | 复核项 |

## 2. 错误 vs 警告

- **error（阻断定稿为 ok）**：`measurement_mismatch`、`citation_mismatch`、`figure_mismatch`、`missing_evidence`、`empty_revised`、`fact_map_empty`。修复后必须重新 `verify` 至 `errors == 0`。
- **warning（需处理但不阻断）**：`hedge_removed`（写 `hedge_note` 回退或说明）、`term_variant`（全文统一为规范写法）、`abbrev_unexpanded`（补首次全称）、`claim_evidence_suggested`（建议补 quote）、`fact_map_partial`（补条目）。warning 全部清零后才给 `status=ok`。
- **resolved**：warning 已由 Agent 提供说明（如 `hedge_note`），保留在 `version_boundary` 的 `human_confirmed` 中，供第三方复核。

## 3. 归一化细节

- 数字：`1,234`→`1234`；`0.50`→`0.5`；`3.2×10^5`→`3.2e5`。注意 `3.2e5` 与 `320000` 不自动等同，表示变化需要显式理由（宁严勿漏）。
- 单位：`_UNIT_CANON` 只合并无语义差别的变体（`μ/µ`、`℃/°C`、`hr/h`、`days/day`、`mL/ml`、`ug/μg`），保留 `M/m`、`K/k`、`G/g`、`S/s` 等语义差别。
- 引用：去内部空白后再比较多重集；`(2020)`→`(2021)` 或 `[1]`→`[2]` 均报 error。
- 图表指向：`Fig. 1` 与 `Figure 1` 视为同一指向（类别词归一），但 `1`→`2` 报 error。
- 限定词：屈折归一避免时态变化误报；`about` 等量化限定词只在其后紧跟数字时计（`about the method` 不算）。

## 4. 证据溯源要求

- 含数字/引用/图表指向的句子：`evidence.locator`（材料路径 + `#段落`）+ `evidence.quote`（逐字引用）**必填**，否则 `missing_evidence`。
- `fact_source_map` 每项含 `fact`/`locator`/`quote`；条目数应覆盖所有需溯源单元。
- 冲突信息全部保留并标 `source_conflict`；缺口写 `MATERIAL GAP`/`not_reported`，不编造。

## 5. 确定性约定

- 所有检查函数为纯函数；报告按 `(unit_index, code)` 稳定排序。
- `finalize` 只读 `/app/work` 状态、只写 `/app/output`，可重复执行；`result.json` 不写时间戳，两次定稿字节一致。
- 词表（单位/限定词/主张动词）可编辑扩展：修改 `checks.py` 中对应常量即可，不影响确定性。
