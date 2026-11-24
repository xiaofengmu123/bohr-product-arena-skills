# 证据模型与守恒校验语义

本文件说明 `evidence.py` 产出的 `evidence_map` 结构、`kind` 枚举与 `verify`
的判定语义，供使用者与复核者逐条核对。所有规则确定性、可复现。

## 1. evidence_map 记录结构

```json
{
  "id": "ev-003",
  "kind": "number",
  "quote": "35.2 g/L",
  "locator": "materials/paper.md#L12",
  "offset": [104, 113],
  "extra": {"number": "35.2", "unit": "g/L"}
}
```

- `id`：稳定编号 `ev-001` 起，按源文本出现顺序分配。
- `quote`：源材料中的**逐字片段**（不做归一化、不改写）。
- `locator`：`文件相对路径#L行号`，可回源定位。
- `offset`：`[start, end)` 字符区间（Python 切片语义），用于把证据绑定到句。
- `extra`：按 `kind` 附加的类型专属字段（如 number 的 `number`/`unit`）。

## 2. kind 枚举与抽取来源

| kind | 含义 | 抽取规则 |
|---|---|---|
| `number` | 数字 + 可选单位 | 正则匹配数字（含小数/千分位/科学计数），后接可选单位词 |
| `citation` | 引用编号或作者-年份 | `[1]`、`[1,2]`、`[1–3]`、`(Smith 2020)`、`(Smith et al. 2020)` |
| `figure` / `table` | 图表指向 | `Fig. 1`、`Figure 1A`、`Table 2`、`图1`、`表2` |
| `hedge` / `strong` | 限定强度 | 封闭词表（中英），如 may/suggest/可能/提示 vs prove/confirm/证实/证明 |
| `negation` | 否定范围 | not/no/never/不/未/无/非 等 |
| `quantifier` | 量词 | all/some/most/few/全部/部分/多数/少数 等 |
| `causal` / `correlational` | 因果 vs 相关 | cause/导致 vs correlate/相关 等封闭词表 |
| `condition` | 条件句 | if/when/unless/若/如果/除非 等 |
| `term` | 缩写定义 | `Full Name (ABBR)` 或 `ABBR (Full Name)` 模式 |

数字与图表指向若落在引用/图表区间内则不重复抽取，避免 `Fig. 1` 中的 `1`
与 `(Smith 2020)` 中的 `2020` 被误计为独立数字。

## 3. verify 判定语义

对每个 edit unit（`original` vs `revised`）分别求信号集，逐项求差：

- **error**：数字/单位丢失（`number_lost`）、引用丢失（`citation_lost`）、
  图表指向丢失（`figure_lost`）、否定丢失（`negation_lost`）、句子未填
  （`unit_unfilled`）、修改类型缺失（`edit_type_missing`）。error 必须清零。
- **warning**：强度增强/减弱（`strength_increased`/`strength_weakened`）、
  相关改因果（`correlation_to_causation`）、条件句删除（`condition_removed`）、
  量词删除（`quantifier_removed`）、无证据链接（`no_evidence_link`）、
  无修改理由（`reason_missing`）。warning 逐条"回退或写理由"。

数字比较先去除千分位并转小写再集合求差；引用与图表比较先去除空白并
归一化全角括号后求差。这样"12,500"与"12500"、"Fig. 1"与"fig.1"不会误报。

## 4. 一致性要求

- 同一输入两次运行结果一致：无随机数、无时间相关布局（`runtime` 仅元数据）。
- 证据只来自已解析的真实材料；缺失标 `pending_human` / `not_reported`。
- 两个来源冲突时同时记录并标 `source_conflict`，不静默选边。
