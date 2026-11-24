# 引用管理规则（Agent 语义层）

本文件规定确定性脚本不做、而由 Agent 按 `SKILL.md` 完成的语义判断。规则要求“证据先行”：每个结论都要能指回材料中的逐字片段。

## 1. 引用插入与定位（挂得对）

- 只给**需要来源支撑的论断句**挂引用，禁止整段末尾“一引了之”。
- 定位规则：
  - 顺序编码制：标记放在论断句结束标点前、紧邻句号处（中文）或句末句点前（英文）。
  - 一句多引用 `[1,2]`；连续多引用 `[3-5]`；不连续用 `[1,3]`。
  - 多句共用同一来源：只在该段首次论断处挂一次，后续句不再重复编号（除非语义需要）。
- 每个新插入的引用都要在 `evidence_map.json` 记一条 `locator + quote + 匹配依据`。

## 2. 编号更新与交叉引用（编得顺）

- 插入/删除/调序后，正文标记、文末列表、图表注、附录引用必须同步重排，编号连续、上标样式统一。
- 重排顺序：按正文中**首次出现**顺序递增；文末列表严格按新编号排序。
- 交叉引用（如“见表 1”“见公式 (3)”）不得与文献编号混淆。

## 3. 文献去重与归并（收得净）

- 判据四级置信度（`core_impl.py` 的 `deduplicate`）：
  1. DOI 相同 → 直接合并（confidence 4）
  2. 规范化题名 + 第一作者姓氏 + 年份相同 → 合并（confidence 3）
  3. 规范化题名 + 年份相同 → 合并但标 `needs_review`（confidence 2）
  4. 仅题名相同 → 不自动合并，标 `needs_review`（confidence 1）
- 同名作者不同文献（题名/年份不同）**不得误合并**；字段冲突不静默覆盖，写进 `merge_evidence.field_updates` 并标 `source_conflict`。

## 4. 格式转换（收得净 / 编得顺）

- 目标规范 `gbt7714`（GB/T 7714—2015）或 `apa`；中文文献按中文格式、英文按英文格式。
- 保留 `DOI / 卷 / 期 / 页 / 文献类型标识`；缺字段标 `not_reported`，禁止编造。

## 5. 来源核验与一致性（对得上）

- 每条引用必须落到文献库**真实条目**；DOI 不能验证时标 `not_verified`。
- 逐句判定“正文论断 ↔ 所引文献主题/结论”是否匹配；不匹配标 `source_conflict` 或 `pending_human`。
- 检出编造条目：无法在材料/可验证来源中找到的条目，标 `not_verified` 并列入 `pending_human`，不删除、不伪造。
- 证据不足的句子显式标 `evidence_insufficient`。

## 6. evidence_map 条目模板

```json
{
  "claim_locator": "materials/manuscript.md#p3",
  "claim_quote": "……论断句逐字原文……",
  "citation_number": 2,
  "matched_entry_key": "alpha",
  "match_basis": "title+first_author+year",
  "matched_entry_quote": "Smith, John … RNA structure prediction … 2020",
  "status": "matched | not_verified | source_conflict | evidence_insufficient"
}
```

`locator` 采用 `相对路径#段落/行` 定位；`quote` 必须逐字来自材料，禁止转述。
