# 语义核验规则（Agent 专用）

本文件规定 `scripts/main.py` 之外、必须由 Agent 完成的语义判断；脚本只提供确定性骨架，不臆造这些结论。每项判断都要回写 `evidence_map.json` 与 `result.json` 的 `change_log`/`uncertainty`。

## 1. 论断句识别与引用定位（挂得对）

1. 逐句扫描稿件，区分“事实/数值/方法/结论”论断与过渡句、常识句。只有需要来源支撑的论断才挂引用。
2. 引用插在论断句的句末标点之前，不整段末尾、不图表注附近；一句多引按贡献主次排号，多句一引放在最能概括的位置。
3. 顺序编码制（GB/T 7714）用上标 `[n]`；著者—出版年制（APA）用 `(Author, Year)`；脚注制单独标记。同一篇稿件只采用一种体例。
4. 输出 `claim_id`（如 `C01`）+ `claim_text`（逐字引用）+ 建议引用编号。

## 2. 编号连锁更新（编得顺）

1. 以 `scripts/main.py` 输出的 `renumber_plan.json` 为基准：编号按正文首次出现顺序重排，正文标记、文末列表序号、图表注、附录引用同步。
2. 检查连续引用合并（`[1,2]` → `[1-2]`）、跨节引用、上标样式一致。
3. 插入/删除引用后重新运行编号，不让手工改动绕过脚本的确定性重排。

## 3. 去重与条目归并（收得净）

1. 脚本按 DOI 相等、规范化题名（去标点/空白/大小写）+ 第一作者姓氏 + 年份 相等做精确合并，`merge_evidence` 记录命中键。
2. 近重复但判据不足（题名近似、年份不同、作者拼写差异、预印本 vs 正式版无 DOI 关联）→ 不自动合并，标 `pending_human`，并给出两条例证。
3. 同名不同文献、同一作者同一年多篇 → 用 DOI 或题名区分，绝不凭作者+年份误合并。

## 4. claim↔文献匹配（对得上）

1. 每条引用需确认：所引文献主题/结论与论断句一致，不是“沾边引用”。
2. `evidence_map.json` 条目：`{claim_id, claim_text, citation_id, locator, quote, match_basis}`；`quote` 为文献库条目或稿件中的逐字片段，`locator` 如 `libs.bib#entry3` 或 `paper.md#L12`。
3. 匹配依据（`match_basis`）写清是 DOI、题名、作者年份还是内容比对；无法确认写 `not_verified`。

## 5. 来源核验

1. DOI 可用运行时检索工具核验；核验失败不声称已核验，标 `not_verified`。
2. 检出编造条目或元数据错配 → 记 `source_conflict`，不静默删除。
3. 证据不足的句子 → `pending_human`；缺字段 → `not_reported`。

## 6. 格式转换映射（GB/T 7714—2015 / APA）

| 文献类型 | GB/T 7714 标识 | APA 处理 |
|---|---|---|
| 期刊论文 | `[J]` | `Author (Year). Title. Journal, Vol(Issue), pages. DOI` |
| 专著 | `[M]` | `Author (Year). Title. Publisher.` |
| 会议论文 | `[C]` | 视作编辑卷章 |
| 学位论文 | `[D]` | `[Doctoral dissertation]` |
| 报告 | `[R]` | `[Report]` |
| 电子文献 | `[EB/OL]` | 附 URL 与访问日期 |

保留 DOI、卷、期、页、文献类型标识等关键字段；中英文混排与标点差异按目标规范处理，脚本输出为 best-effort，Agent 复核字段完整性。
