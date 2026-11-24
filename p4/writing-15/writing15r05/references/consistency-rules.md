# 一致性检查与证据溯源规则

本文说明 `scripts/consistency.py` 与 `scripts/evidence.py` 的检查口径、判定阈值与
处理约定。两个脚本只做**确定性、带 locator 的筛查**，最终是否修改由 Agent 决定；
问题以 `severity`（info/warning/error）标注，不替 Agent 作裁决。

## 1. 一致性检查（consistency.py）

| 检查项 | 判定 | 处理约定 |
|-|-|-|
| heading_numbering | 章节编号出现缺口或同一编号出现多次 | 补号或合并；无法确定则标 uncertainty |
| figure_table_numbering | 图/表/公式编号缺口或重复 | 重排编号，并同步正文交叉引用 |
| term_variant | 同一拉丁术语归一化后多种写法（如 Long-Context / LongContext） | 取首次定义写法，全文统一，记入 change_log |
| duplicate_definition | 同一术语多处定义且措辞不一致 | 保留最早/最完整定义，删冗余或合并，标注 |
| citation_undefined | `[n]` 引用但参考文献表无该条目 | 补齐条目；找不到原文 → pending_human |
| citation_uncited | 参考文献有条目但正文从未引用 | 删除或补引，标注 |
| numeric_conflict | 同一句式模板下出现不同数值 | 以给定材料为准改回；两处都来自材料 → source_conflict |

- 输入：`--chapters <目录>`，只读 `.md/.txt/.tex/.rst` 文本文件。
- 输出：`check_report.json`（机器可读）与 `check_report.md`（人可读），退出码恒为 0。
- 判定为启发式：可能产生误报（尤其 numeric_conflict）。Agent 必须回读原文确认后再修改。

## 2. 证据溯源（evidence.py）

- `seed`：扫描材料，抽取候选 DOI、数字、术语-缩写对、引用键，生成 `evidence_map.json`
  草稿（quote 为所在整行，claim 留空待填）。
- `validate`：逐条校验 claim/file/quote/locator 齐全性、quote 是否逐字出现在原文、
  locator 行号是否匹配、同一 claim 是否多来源且 quote 不一致（source_conflict）。

### evidence_map.json 约定

```json
{"entries": [
  {"id": "e1", "claim": "论断（一句）", "file": "materials/ch1.md",
   "locator": "materials/ch1.md#L12", "quote": "与原文逐字一致"}
]}
```

- `file` 与 `locator` 用相对材料根目录的 POSIX 路径；`quote` 必须逐字（只允许空白归一化）。
- 校验不通过：优先修正 quote/locator；无法修正则把该条移入 `uncertainty` 或
  `pending_human`，并在 result.json 中说明，绝不删除证据后声称已核验。

## 3. 判定边界

- 材料不足、来源缺失、结论冲突，一律写实标注；`status` 用
  `ok / partial / insufficient_evidence / input_error` 之一，缺证据不用 `ok`。
- 脚本只依赖 Python 标准库，固定输入固定输出（同一材料两次运行结果一致），无随机性。
