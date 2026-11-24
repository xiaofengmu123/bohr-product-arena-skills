# 引用管理规则（脚本与 Agent 共同遵循，可独立复用）

本文件规定确定性脚本与 Agent 语义判断的边界，以及可解释的去重判据、编号规则、格式映射与证据溯源格式。

## 1. 分工边界

| 工作 | 执行者 | 依据 |
|---|---|---|
| 文件发现、容错读取、解析、规范化 | `scripts/main.py`（`core_impl.py`） | 纯确定性 |
| 去重合并（union-find，含合并依据） | 脚本 | 本文件第 2 节 |
| 编号重排（按首次出现序、按唯一作品分配） | 脚本 | 本文件第 3 节 |
| 格式转换（GB/T 7714 / APA） | 脚本 | 本文件第 4 节 |
| 一致性检查（未解决/未引用/编号问题） | 脚本 | 本文件第 5 节 |
| 论断句识别、引用位置选择（`[#cN]` 占位） | Agent | 语义，写 `citation_plan.json` |
| claim ↔ 文献匹配、来源真伪核验 | Agent | 语义，写 `citation_plan.json` + `evidence_map.json` + `change_log` |

## 2. 去重判据（可解释，union-find 联合求并）

条目以两条键链式归并，链式链接可让"DOI 相同"与"题名签名相同"两类证据聚成同一簇：

1. **DOI 键**：规范化 DOI（去掉 `https://doi.org/` 前缀、小写、去尾句点）精确相等。
2. **签名键**：`规范化题名 + 年份 + 第一作者姓氏`。题名规范化 = 去 BibTeX 大小写保护 `{}`、去非字字符、小写、按空格折叠；第一作者取 `and`/`;` 分割后首个名字的姓氏（逗号在左取左、否则取末词）。

合并规则：

- 组内多条目合并为一条，`merge_evidence` 记录 `merged_key`、`count`、`criterion`、`source_records`、`kept_fields_from`。
- 以"字段最全"的条目为基底，缺失字段从重复条目回填；`alias_map` 把每个原始 `_key/id/title` 映射到合并后条目，保证引用到被并条目仍能解析。
- **不合并**：题名规范化后不同、第一作者姓氏不同、年份明显不同（跨年重印/增订标 `pending_human`）。
- DOI 相同但题名不同：仍按 DOI 合并（DOI 唯一标识一篇作品），题名差异保留在 `source_records` 供人工复核。

## 3. 编号重排规则

- Agent 在稿件论断句位置插入占位符 `[#c1]`、`[#c2,c3]`（一句多引用逗号），脚本替换为顺序编码 `[1]`、`[2-3]`（区间折叠）。
- 新编号 = 占位符在正文中的**首次出现顺序**（1-based）。
- **同一文献多条论断共享一个编号**：脚本先解析每个引用 id 到去重后的条目，再按"唯一作品"分配编号，条目只出现一次。
- 交叉引用/图表注/附录中的同一占位符跟随同一映射，保证连锁更新；纯上标数字、`\cite{}`、著者—出版年制的插入位置由 Agent 判定，脚本不强改。

## 4. 格式映射（GB/T 7714—2015 顺序编码制 / APA）

类型标识（GB/T 7714）：

| 来源 type | 标识 |
|---|---|
| article / journal | `[J]` |
| inproceedings / conference / incollection | `[C]` |
| book | `[M]` |
| phdthesis / mastersthesis / thesis | `[D]` |
| 其他（misc 等） | `[EB/OL]` 或原 type |

GB/T 7714 顺序编码制：`[n] 作者. 题名[类型]. 刊名, 年, 卷(期): 页.`；作者名姓在前、名缩写首字母（如 `Smith J`），末尾附 `https://doi.org/...`。APA：`作者, 首字母. (年). 题名. 刊名, 卷(期), 页. https://doi.org/...`；1 名/2 名/多名的 `&` 连接规则按 APA 子集实现。转换前后保留 DOI、卷期页、文献类型标识，字段缺失留空不补造，中英文混排与标点差异按目标规范处理。

## 5. 一致性检查口径

- `unresolved_citations`：plan 中 `entry_id` 无法解析到文献库条目（含被并条目的 alias 也无法命中）。
- `uncited_entries`：文献库有条目但未被任何引用绑定。
- `numbering_issues`：占位符在正文中的出现位置记录（供人工核对编号是否连续）。
- 未找到稿件时 `manuscript.md` 为空占位符，脚本如实降级。

## 6. 证据溯源格式（evidence_map.json）

每条引用记录形如：

```json
{
  "citation_id": "c1",
  "number": 1,
  "entry_id": "smith2020",
  "locator": "manuscript.md#L12",
  "quote": "……被支撑论断的逐字片段……",
  "match_reason": "该文献主题/结论如何支撑此论断",
  "verified": false
}
```

`locator + 逐字 quote` 是硬要求；冲突同时记录双方并标 `source_conflict`；无法核验标 `not_verified`/`pending_human`，`verified` 仅在语义核验完成后由 Agent 置真，脚本不代置。

## 7. 失败模式速查（供复用）

| 现象 | 判据 | 处置字段 |
|---|---|---|
| 无稿件/无文献库 | 发现列表为空 | `status=insufficient_evidence` + `pending_human` |
| 坏 JSON/坏编码 | 读取异常 | `errors`，继续其余材料 |
| 超 2MB / 未知扩展 | 尺寸/后缀 | `degradations`，跳过 |
| 同一文献多 DOI（预印本 vs 正式版） | DOI 或签名键命中 | 合并，`merge_evidence.criterion` 注明 |
| 同名一作+同年但题名不同 | 签名键不命中 | 不合并，`pending_human` |
| 引用条目无法解析 | entry_id 不在库且 alias 未命中 | `consistency_report.unresolved_citations` |
| 列表有条目未被引用 | 库条目未绑定任何引用 | `consistency_report.uncited_entries` |
